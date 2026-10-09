/**
 * Claude assistant in the right rail on every tab (docs/phases/PHASE3.md sections 4 and 8).
 *
 * The conversation is stored per project on the server. A question is POSTed and the answer
 * streams back as Server-Sent Events (read with fetch and a stream reader, since EventSource
 * cannot POST): `text` chunks are appended as they arrive, `tool_call` labels show as small chips
 * while the engine works, `proposal` events become cards with "Try as new version", and `done` or
 * `error` end the turn. On a refusal the partial text is dropped and only the plain message
 * stays. The panel expands into a larger drawer (always available; the way in on phones).
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import type { AssistantStatus, Proposal, ThreadMessage, ThreadResponse, ToolCallInfo } from '../api/analysis';
import { ApiError, api, apiStream, errorMessage, isAbortError, isAuthError } from '../api/client';
import type { DraftDocument } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { describeChange, truncateName } from '../lib/analysis';
import { getAtPath } from '../lib/draft';
import { readSse } from '../lib/sse';
import { useWorkspace } from '../lib/workspace';

type ChipState = 'running' | 'done' | 'error';

interface UiMessage {
  key: string;
  role: 'user' | 'assistant' | 'notice';
  text: string;
  tools: Array<ToolCallInfo & { state: ChipState }>;
  proposals: Proposal[];
  code?: string;
  streaming?: boolean;
}

let keySeq = 0;
const nextKey = () => `m${(keySeq += 1)}`;

function fromThread(m: ThreadMessage): UiMessage {
  if (m.role === 'assistant') {
    return {
      key: `s${m.id}`,
      role: 'assistant',
      text: m.text,
      tools: m.tool_calls.map((t) => ({ ...t, state: t.is_error ? 'error' : 'done' })),
      proposals: m.proposals,
    };
  }
  if (m.role === 'notice') return { key: `s${m.id}`, role: 'notice', text: m.text, tools: [], proposals: [], code: m.code };
  return { key: `s${m.id}`, role: 'user', text: m.text, tools: [], proposals: [] };
}

function parseData(data: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(data);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function formatValue(v: unknown): string {
  if (typeof v === 'number') return v.toLocaleString('en-IE', { maximumFractionDigits: 3 });
  return String(v);
}

function ProposalCard({
  proposal,
  doc,
  busy,
  onTry,
}: {
  proposal: Proposal;
  doc: DraftDocument;
  busy: boolean;
  onTry: () => void;
}) {
  return (
    <div className="assistant-proposal" data-testid="assistant-proposal">
      <div className="assistant-proposal-title small">Suggested change</div>
      <p className="small">{proposal.summary}</p>
      <ul className="assistant-patch small">
        {Object.entries(proposal.patch).map(([path, value]) => {
          const current = getAtPath(doc, path.startsWith('mission.') ? path : `parameters.${path}`);
          return (
            <li key={path}>
              <span className="mono">{path}</span>: {current !== undefined ? `${formatValue(current)} → ` : ''}
              <strong>{formatValue(value)}</strong>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        className="button button-sm button-primary"
        data-testid="assistant-proposal-try"
        disabled={busy}
        onClick={onTry}
      >
        {busy ? 'Saving…' : 'Try as new version'}
      </button>
    </div>
  );
}

export function AssistantPanel({ projectId, doc }: { projectId: number; doc: DraftDocument }) {
  const workspace = useWorkspace();
  const inputId = useId();
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [trying, setTrying] = useState<string | null>(null);
  const streamRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<AssistantStatus>('/api/assistant/status', { signal: controller.signal })
      .then(setStatus)
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setStatusError(errorMessage(caught));
      });
    api<ThreadResponse>(`/api/projects/${projectId}/assistant/messages`, { signal: controller.signal })
      .then((thread) => {
        setMessages(thread.messages.map(fromThread));
        setLoaded(true);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setLoaded(true);
      });
    return () => controller.abort();
  }, [projectId]);

  // Abort a running answer when the panel goes away (another project, sign-out).
  useEffect(
    () => () => {
      streamRef.current?.abort();
    },
    [],
  );

  // Keep the newest message in view while an answer streams.
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages]);

  // Escape closes the drawer.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [expanded]);

  const patchAssistant = useCallback((key: string, change: (m: UiMessage) => UiMessage) => {
    setMessages((list) => list.map((m) => (m.key === key ? change(m) : m)));
  }, []);

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = text.trim();
    if (!question || busy) return;
    if (question.length > 4000) {
      setFormError('Questions can be at most 4000 characters.');
      return;
    }
    setFormError(null);
    setBusy(true);
    const userKey = nextKey();
    const answerKey = nextKey();
    setMessages((list) => [
      ...list,
      { key: userKey, role: 'user', text: question, tools: [], proposals: [] },
      { key: answerKey, role: 'assistant', text: '', tools: [], proposals: [], streaming: true },
    ]);
    setText('');
    const controller = new AbortController();
    streamRef.current = controller;
    let ended = false;
    try {
      const response = await apiStream(`/api/projects/${projectId}/assistant/messages`, { text: question }, controller.signal);
      await readSse(
        response,
        (ev) => {
          const data = parseData(ev.data);
          switch (ev.event) {
            case 'text':
              patchAssistant(answerKey, (m) => ({ ...m, text: m.text + String(data.text ?? '') }));
              break;
            case 'tool_call':
              patchAssistant(answerKey, (m) => ({
                ...m,
                tools: [
                  ...m.tools.map((t) => (t.state === 'running' ? { ...t, state: 'done' as const } : t)),
                  {
                    id: String(data.id ?? nextKey()),
                    name: String(data.name ?? ''),
                    label: String(data.label ?? 'Working…'),
                    state: 'running',
                  },
                ],
              }));
              break;
            case 'proposal':
              patchAssistant(answerKey, (m) => ({
                ...m,
                tools: m.tools.map((t) => (t.state === 'running' ? { ...t, state: 'done' as const } : t)),
                proposals: [
                  ...m.proposals,
                  { patch: (data.patch as Record<string, unknown>) ?? {}, summary: String(data.summary ?? ''), base: 'draft' },
                ],
              }));
              break;
            case 'done':
              ended = true;
              patchAssistant(answerKey, (m) => ({
                ...m,
                streaming: false,
                tools: m.tools.map((t) => (t.state === 'running' ? { ...t, state: 'done' as const } : t)),
              }));
              break;
            case 'error': {
              ended = true;
              const code = String(data.code ?? 'internal');
              const message = String(data.message ?? 'The assistant could not answer.');
              setMessages((list) => {
                const answer = list.find((m) => m.key === answerKey);
                // A refusal's partial text is not part of the conversation: drop it.
                const keep =
                  answer && code !== 'refusal' && (answer.text.trim() || answer.proposals.length)
                    ? [
                        {
                          ...answer,
                          streaming: false,
                          tools: answer.tools.map((t) => (t.state === 'running' ? { ...t, state: 'error' as const } : t)),
                        },
                      ]
                    : [];
                return [
                  ...list.filter((m) => m.key !== answerKey),
                  ...keep,
                  { key: nextKey(), role: 'notice', text: message, tools: [], proposals: [], code },
                ];
              });
              break;
            }
            default:
              break;
          }
        },
        controller.signal,
      );
      if (!ended) {
        // The stream closed without done or error (connection dropped).
        patchAssistant(answerKey, (m) => ({ ...m, streaming: false }));
        setMessages((list) => [
          ...list,
          {
            key: nextKey(),
            role: 'notice',
            text: 'The connection closed before the answer finished. Ask again to continue.',
            tools: [],
            proposals: [],
            code: 'internal',
          },
        ]);
      }
    } catch (caught) {
      if (isAbortError(caught)) return;
      // Nothing was streamed: remove the empty answer and show the plain message.
      const message =
        caught instanceof ApiError && caught.status === 409
          ? caught.hasDetail
            ? caught.detail
            : 'The assistant is still answering another question in this project. Wait for it to finish.'
          : errorMessage(caught);
      setMessages((list) => [
        ...list.filter((m) => !(m.key === answerKey && !m.text)),
        {
          key: nextKey(),
          role: 'notice',
          text: message,
          tools: [],
          proposals: [],
          code: caught instanceof ApiError && caught.status === 409 ? 'busy' : 'api_error',
        },
      ]);
      if (!isAuthError(caught) && caught instanceof ApiError && caught.status === 409) setText(question);
    } finally {
      if (streamRef.current === controller) streamRef.current = null;
      setBusy(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  };

  const clear = async () => {
    setClearing(true);
    try {
      await api<void>(`/api/projects/${projectId}/assistant/messages`, { method: 'DELETE' });
      setMessages([]);
      setConfirmClear(false);
    } catch (caught) {
      if (!isAuthError(caught)) setFormError(errorMessage(caught));
      setConfirmClear(false);
    } finally {
      setClearing(false);
    }
  };

  const tryProposal = async (key: string, proposal: Proposal) => {
    setTrying(key);
    const parts = Object.entries(proposal.patch).map(([path, value]) => {
      const from = getAtPath(doc, path.startsWith('mission.') ? path : `parameters.${path}`);
      return describeChange(path.replace(/^mission\./, ''), from, value);
    });
    await workspace.tryAsNewVersion({
      name: truncateName(`Assistant: ${parts.join(', ') || 'suggestion'}`),
      notes: proposal.summary,
      base: 'draft',
      patch: proposal.patch,
      compareWith: workspace.basisNumber,
    });
    setTrying(null);
  };

  const available = status?.available ?? false;

  return (
    <>
      <section
        className={`card assistant-panel${expanded ? ' assistant-drawer' : ''}`}
        data-testid="assistant-panel"
        aria-labelledby="assistant-heading"
        role={expanded ? 'dialog' : undefined}
        aria-modal={expanded ? true : undefined}
      >
        <div className="card-header">
          <h2 id="assistant-heading">Assistant</h2>
          {messages.length > 0 ? (
            <button
              type="button"
              className="button button-ghost button-sm"
              data-testid="assistant-clear"
              disabled={busy}
              onClick={() => setConfirmClear(true)}
            >
              Clear
            </button>
          ) : null}
          <button
            type="button"
            className="button button-ghost button-sm"
            data-testid="assistant-expand"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? 'Close' : 'Expand'}
          </button>
        </div>
        <p className="card-note">
          Ask about the design and the analysis. It reads the engine’s numbers and quotes them with their ranges; it never makes
          numbers up. It can try a change with a quick analysis and suggest it as a new version.
        </p>

        <div className="assistant-messages" ref={listRef} data-testid="assistant-messages" aria-live="polite" aria-busy={busy}>
          {!loaded ? <p className="loading small">Loading the conversation…</p> : null}
          {loaded && messages.length === 0 ? (
            <p className="small muted">
              For example: “Why is the endurance below my target?” or “What if the wingspan were 1900 mm?”
            </p>
          ) : null}
          {messages.map((m) =>
            m.role === 'notice' ? (
              <p key={m.key} className="assistant-notice small" role="alert" data-testid="assistant-error" data-code={m.code}>
                {m.text}
              </p>
            ) : (
              <div
                key={m.key}
                className={`assistant-message assistant-${m.role}`}
                data-testid="assistant-message"
                data-role={m.role}
                data-streaming={m.streaming ? 'true' : undefined}
              >
                {m.tools.length ? (
                  <div className="assistant-chips">
                    {m.tools.map((t) => (
                      <span
                        key={t.id}
                        className={`assistant-chip chip-${t.state}`}
                        data-testid="assistant-tool-chip"
                        data-state={t.state}
                        data-tool={t.name}
                      >
                        {t.state === 'running' ? (
                          <span className="chip-spinner" aria-hidden="true" />
                        ) : (
                          <span aria-hidden="true">{t.state === 'error' ? '!' : '✓'}</span>
                        )}
                        {t.label}
                      </span>
                    ))}
                  </div>
                ) : null}
                {m.text ? (
                  <div className="assistant-text">{m.text}</div>
                ) : m.streaming ? (
                  <div className="assistant-text muted">Thinking…</div>
                ) : null}
                {m.proposals.map((p, i) => (
                  <ProposalCard
                    key={i}
                    proposal={p}
                    doc={doc}
                    busy={trying === `${m.key}:${i}`}
                    onTry={() => void tryProposal(`${m.key}:${i}`, p)}
                  />
                ))}
              </div>
            ),
          )}
        </div>

        {statusError ? (
          <p className="form-error small" role="alert">
            Could not reach the assistant: {statusError}
          </p>
        ) : status && !available ? (
          <p className="assistant-unavailable small" data-testid="assistant-unavailable" role="status">
            {status.message ?? 'The assistant is not available.'}
          </p>
        ) : (
          <form className="assistant-form" onSubmit={(e) => void send(e)}>
            <label htmlFor={inputId} className="visually-hidden">
              Ask the assistant
            </label>
            <textarea
              id={inputId}
              className="input assistant-input"
              data-testid="assistant-input"
              rows={2}
              maxLength={4000}
              placeholder={busy ? 'Answering…' : 'Ask a question (Enter to send, Shift+Enter for a new line)'}
              value={text}
              disabled={!status}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <div className="assistant-form-row">
              {busy ? (
                <span className="small muted" data-testid="assistant-busy" role="status">
                  <span className="chip-spinner" aria-hidden="true" /> Answering…
                </span>
              ) : (
                <span className="small faint">{status ? status.model : ''}</span>
              )}
              <button
                type="submit"
                className="button button-primary button-sm"
                data-testid="assistant-send"
                disabled={busy || !text.trim() || !status}
              >
                Send
              </button>
            </div>
            {formError ? (
              <p className="form-error small" role="alert">
                {formError}
              </p>
            ) : null}
          </form>
        )}

        <ConfirmDialog
          open={confirmClear}
          title="Clear the conversation?"
          message="The assistant forgets this project's conversation. Versions and analyses are not affected."
          confirmLabel="Clear conversation"
          danger
          busy={clearing}
          onConfirm={() => void clear()}
          onCancel={() => setConfirmClear(false)}
        />
      </section>
      {expanded ? <div className="assistant-backdrop" onClick={() => setExpanded(false)} aria-hidden="true" /> : null}
      {!expanded ? (
        <button
          type="button"
          className="button button-primary assistant-fab"
          data-testid="assistant-open"
          onClick={() => setExpanded(true)}
        >
          Ask the assistant
        </button>
      ) : null}
    </>
  );
}
