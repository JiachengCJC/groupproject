import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import type { Config } from './types';
import {
  compatibleDecision,
  decisionProviderLabel,
  eligibleDocuments,
  isProbabilisticDecision,
  localProfileIssue,
  retrievalModelLabel,
  parseCandidates,
} from './utils';
afterEach(() => vi.unstubAllGlobals());
describe('API transport contract', () => {
  it('sends authenticated JSON requests and preserves validation detail', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: [{ loc: ['body', 'question'], msg: 'Too short' }] }),
          { status: 422, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    vi.stubGlobal('fetch', fetch);
    await expect(api('/analyses', { question: 'x' })).rejects.toThrow('question: Too short');
    expect(fetch).toHaveBeenCalledWith(
      '/api/analyses',
      expect.objectContaining({
        credentials: 'same-origin',
        method: 'POST',
        body: JSON.stringify({ question: 'x' }),
      }),
    );
  });
  it('does not invent a multipart boundary or expose a reverse proxy HTML error', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response('<html>private proxy error</html>', {
        status: 502,
        headers: { 'Content-Type': 'text/html' },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    const form = new FormData();
    form.append('file', new Blob(['hello']), 'hello.txt');
    await expect(api('/uploads', form)).rejects.toThrow('502');
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty('Content-Type');
  });
  it('expires the workspace when protected requests lose authentication', async () => {
    const events = new EventTarget();
    const listener = vi.fn();
    events.addEventListener('session-expired', listener);
    vi.stubGlobal('window', events);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Sign in again' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    await expect(api('/sources')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledOnce();
  });
});
describe('analysis request constraints', () => {
  it('keeps document selection scoped while preserving shared references', () => {
    const docs = [
      { id: 'shared', title: 'Shared', source: '' },
      { id: 'a', title: 'A', source: '', dataset_id: 'a' },
      { id: 'b', title: 'B', source: '', dataset_id: 'b' },
    ];
    expect(eligibleDocuments(docs, 'a').map((d) => d.id)).toEqual(['shared', 'a']);
    expect(eligibleDocuments(docs, '')).toHaveLength(3);
  });
  it('cannot keep OpenAI Decisions selected in local-only mode or model recommendation with no model', () => {
    expect(compatibleDecision('ollama', 'openai_decisions', true)).toBe('llm');
    expect(compatibleDecision('local', 'openai_decisions', true)).toBe('baseline');
    expect(compatibleDecision('local', 'llm', false)).toBe('baseline');
  });
  it('migrates historical JEV requests and respects unavailable OpenAI credentials', () => {
    expect(compatibleDecision('local', 'jev', false)).toBe('openai_decisions');
    expect(compatibleDecision('ollama', 'jev', false)).toBe('openai_decisions');
    expect(compatibleDecision('ollama', 'jev', true)).toBe('llm');
    expect(compatibleDecision('local', 'jev', false, false)).toBe('baseline');
    expect(compatibleDecision('live', 'openai_decisions', false, false)).toBe('llm');
  });
  it('distinguishes historical providers while preserving probability interpretation', () => {
    expect(decisionProviderLabel('openai_decisions')).toBe('OpenAI Decisions');
    expect(decisionProviderLabel('jev')).toBe('Legacy TypeSafe JEV');
    expect(isProbabilisticDecision('openai_decisions')).toBe(true);
    expect(isProbabilisticDecision('jev')).toBe(true);
    expect(isProbabilisticDecision('baseline')).toBe(false);
    expect(isProbabilisticDecision('ollama')).toBe(false);
  });
  it('creates typed candidate actions and rejects empty or excessive actions', () => {
    expect(parseCandidates('Pilot | Cap spending\nWait')).toEqual([
      { id: 'action_1', title: 'Pilot', description: 'Cap spending' },
      { id: 'action_2', title: 'Wait', description: 'Wait' },
    ]);
    expect(parseCandidates('  ')).toBeUndefined();
    expect(() => parseCandidates('|description')).toThrow();
    expect(() => parseCandidates(Array(13).fill('action').join('\n'))).toThrow();
  });
});

describe('local run mode availability', () => {
  const config: Config = {
    live_agents_available: true,
    openai_decisions_available: true,
    scenarios: [],
    local_profiles: {
      auto: {
        label: 'Local Auto',
        description: 'Choose by task complexity',
        models: { planner: 'fast:4b', analysis: 'quality:9b' },
        required_models: ['fast:4b', 'quality:9b'],
        available: true,
        unavailable_reason: null,
      },
    },
  };
  it('requires every possible Auto model and never silently downgrades', () => {
    expect(localProfileIssue('auto', config, { available: true, models: ['fast:4b'] })).toContain(
      'quality:9b',
    );
    expect(
      localProfileIssue('auto', config, { available: true, models: ['fast:4b', 'quality:9b'] }),
    ).toBeNull();
  });
  it('respects server policy and an unreachable daemon before installation checks', () => {
    const blocked = {
      ...config,
      local_profiles: {
        auto: {
          ...config.local_profiles!.auto!,
          available: false,
          unavailable_reason: 'Configure local embeddings first.',
        },
      },
    };
    expect(
      localProfileIssue('auto', blocked, { available: true, models: ['fast:4b', 'quality:9b'] }),
    ).toBe('Configure local embeddings first.');
    expect(
      localProfileIssue('auto', config, {
        available: false,
        models: [],
        error: 'Ollama unavailable',
      }),
    ).toBe('Ollama unavailable');
    expect(localProfileIssue('fast', config, { available: true, models: ['fast:4b'] })).toContain(
      'not configured',
    );
  });
  it('keeps historical custom selection independent of new profile configuration', () => {
    expect(localProfileIssue('custom', config, null)).toBeNull();
  });
  it('does not present a retained embedding configuration as active during keyword retrieval', () => {
    expect(
      retrievalModelLabel({ ...config, retrieval_mode: 'lexical', embedding_model: 'bge-m3' }),
    ).toBe('Keyword retrieval · No embedding model');
    expect(
      retrievalModelLabel({
        ...config,
        retrieval_mode: 'hybrid',
        embedding_provider: 'ollama',
        embedding_model: 'bge-m3',
      }),
    ).toBe('bge-m3');
  });
});
