import { describe, expect, it } from 'vitest';
import { SseParser, readSse } from './sse';

const STREAM =
  'event: text\ndata: {"text": "Hello"}\n\n' +
  'event: tool_call\ndata: {"id": "t1", "name": "run_quick_analysis", "label": "Running a quick analysis…"}\n\n' +
  ': keep-alive comment\n\n' +
  'event: done\ndata: {"message_id": 6}\n\n';

describe('SseParser', () => {
  it('parses whole events', () => {
    const events = new SseParser().push(STREAM);
    expect(events.map((e) => e.event)).toEqual(['text', 'tool_call', 'done']);
    expect(JSON.parse(events[0].data)).toEqual({ text: 'Hello' });
  });

  it('gives the same events for every split of the stream, one character at a time', () => {
    const parser = new SseParser();
    const events = [...STREAM].flatMap((ch) => parser.push(ch));
    expect(events).toEqual(new SseParser().push(STREAM));
  });

  it('joins multi-line data with newlines and drops one leading space only', () => {
    const events = new SseParser().push('event: text\ndata: line one\ndata:  two\ndata\n\n');
    expect(events).toEqual([{ event: 'text', data: 'line one\n two\n' }]);
  });

  it('handles CRLF and lone CR line ends, including a CRLF split across chunks', () => {
    const parser = new SseParser();
    const a = parser.push('event: a\r');
    const b = parser.push('\ndata: 1\r\n\r');
    const c = parser.push('event: b\rdata: 2\r\r');
    expect([...a, ...b, ...c]).toEqual([
      { event: 'a', data: '1' },
      { event: 'b', data: '2' },
    ]);
  });

  it('defaults the event name, ignores events without data and flushes at the end', () => {
    const parser = new SseParser();
    expect(parser.push('event: lonely\n\ndata: x\n\n')).toEqual([{ event: 'message', data: 'x' }]);
    expect(parser.push('event: done\ndata: {}')).toEqual([]);
    expect(parser.end()).toEqual([{ event: 'done', data: '{}' }]);
  });
});

describe('readSse', () => {
  it('decodes UTF-8 split inside a character and delivers events in order', async () => {
    const bytes = new TextEncoder().encode('event: text\ndata: {"text": "Ä…é"}\n\nevent: done\ndata: {}\n\n');
    const chunks = [bytes.slice(0, 24), bytes.slice(24, 27), bytes.slice(27)];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    });
    const seen: string[] = [];
    await readSse(new Response(body), (e) => seen.push(`${e.event}:${e.data}`));
    expect(seen).toEqual(['text:{"text": "Ä…é"}', 'done:{}']);
  });

  it('stops with an AbortError when aborted', async () => {
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('event: text\ndata: {}\n\n'));
      },
    });
    const done = readSse(new Response(body), () => controller.abort(), controller.signal);
    await expect(done).rejects.toThrow(/abort/i);
  });
});
