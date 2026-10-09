/**
 * Incremental Server-Sent Events parser for streamed POST responses (EventSource only does
 * GET). Feed it text chunks as they arrive, in any split: an event is emitted only once its
 * terminating blank line has been seen. Follows the WHATWG event-stream rules that matter
 * here: CRLF, LF or CR line ends; `event:` names the event (default "message"); several
 * `data:` lines join with "\n"; one space after the colon is dropped; lines starting with ":"
 * are comments; a blank line dispatches; an event with no data is ignored.
 */

export interface SseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = '';
  private eventName = '';
  private dataLines: string[] = [];
  private hasData = false;
  /** A chunk ended in "\r": the next chunk may start with the "\n" of the same CRLF. */
  private pendingCr = false;

  /** Parse a chunk and return the events it completed. */
  push(chunk: string): SseEvent[] {
    let text = chunk;
    if (this.pendingCr && text.startsWith('\n')) text = text.slice(1);
    this.pendingCr = false;
    this.buffer += text;
    const events: SseEvent[] = [];
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      if (!match) break;
      // A lone "\r" at the very end may be the first half of "\r\n" split across chunks.
      if (match[0] === '\r' && match.index === this.buffer.length - 1) {
        this.pendingCr = true;
      }
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const event = this.line(line);
      if (event) events.push(event);
    }
    return events;
  }

  /** Flush at end of stream: a final event without its blank line is still delivered. */
  end(): SseEvent[] {
    const events: SseEvent[] = [];
    if (this.buffer) {
      const event = this.line(this.buffer);
      this.buffer = '';
      if (event) events.push(event);
    }
    const last = this.dispatch();
    if (last) events.push(last);
    return events;
  }

  private line(line: string): SseEvent | null {
    if (line === '') return this.dispatch();
    if (line.startsWith(':')) return null;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.eventName = value;
    else if (field === 'data') {
      this.dataLines.push(value);
      this.hasData = true;
    }
    return null;
  }

  private dispatch(): SseEvent | null {
    const event = this.hasData ? { event: this.eventName || 'message', data: this.dataLines.join('\n') } : null;
    this.eventName = '';
    this.dataLines = [];
    this.hasData = false;
    return event;
  }
}

/**
 * Read a streaming response body to the end, calling `onEvent` for each event in order.
 * Bytes are decoded as UTF-8 with streaming so multi-byte characters split across chunks
 * survive. Rejects with an AbortError when the signal aborts.
 */
export async function readSse(response: Response, onEvent: (event: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  const body = response.body;
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener('abort', onAbort);
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      if (done) break;
      for (const event of parser.push(decoder.decode(value, { stream: true }))) onEvent(event);
    }
    for (const event of parser.push(decoder.decode())) onEvent(event);
    for (const event of parser.end()) onEvent(event);
  } finally {
    signal?.removeEventListener('abort', onAbort);
    try {
      reader.releaseLock();
    } catch {
      // already released or a read is still settling; nothing to do
    }
  }
}
