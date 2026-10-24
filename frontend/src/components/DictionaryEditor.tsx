import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import type { Dataset } from '../types';
import { Loading, Notice } from './ui';
interface Dictionary {
  description: string;
  currency: string;
  timezone: string;
  period_definition: string;
  columns: Record<string, string>;
  metrics: Record<string, string>;
}
export default function DictionaryEditor({ dataset }: { dataset: Dataset }) {
  const { notify } = useWorkspace();
  const { canWrite } = usePermissions();
  const [value, setValue] = useState<Dictionary | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [metrics, setMetrics] = useState('');
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    api<Partial<Dictionary>>(
      '/datasets/' + encodeURIComponent(dataset.id) + '/dictionary',
      undefined,
      { signal: controller.signal },
    )
      .then((result) => {
        if (!active) return;
        setValue({
          description: '',
          currency: '',
          timezone: 'UTC',
          period_definition: '',
          columns: {},
          metrics: {},
          ...result,
        });
        setMetrics(
          Object.entries(result.metrics || {})
            .map(([key, expression]) => key + ' = ' + expression)
            .join('\n'),
        );
      })
      .catch((err) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [dataset.id]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!value) return;
    setBusy(true);
    try {
      const definitions: Record<string, string> = {};
      for (const line of metrics
        .split('\n')
        .map((text) => text.trim())
        .filter(Boolean)) {
        const index = line.indexOf('=');
        if (index < 1 || !line.slice(index + 1).trim())
          throw new Error('Use “name = calculation” for each metric, with one metric per line.');
        const name = line.slice(0, index).trim();
        if (definitions[name]) throw new Error('Metric names must be unique.');
        definitions[name] = line.slice(index + 1).trim();
      }
      await api(
        '/datasets/' + encodeURIComponent(dataset.id) + '/dictionary',
        {
          description: value.description,
          currency: value.currency,
          timezone: value.timezone,
          period_definition: value.period_definition,
          columns: value.columns,
          metrics: definitions,
        },
        { method: 'PUT' },
      );
      notify('Business definitions saved. Future analyses will use the updated definitions.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="dictionary-editor">
      <summary>Business definitions and data dictionary</summary>
      {error ? (
        <Notice error>{error}</Notice>
      ) : !value ? (
        <Loading />
      ) : (
        <form onSubmit={save}>
          <p className="help">
            Define units, reporting periods, and field meanings for consistent queries and
            explanations.
          </p>
          <label>
            Dataset description
            <textarea
              value={value.description}
              onChange={(e) => setValue({ ...value, description: e.target.value })}
              rows={2}
              maxLength={2000}
              disabled={!canWrite}
            />
          </label>
          <div className="form-grid">
            <label>
              Currency
              <input
                value={value.currency}
                onChange={(e) => setValue({ ...value, currency: e.target.value })}
                placeholder="For example: SGD"
                maxLength={20}
                disabled={!canWrite}
              />
            </label>
            <label>
              Timezone
              <input
                value={value.timezone}
                onChange={(e) => setValue({ ...value, timezone: e.target.value })}
                placeholder="Asia/Singapore"
                maxLength={80}
                disabled={!canWrite}
              />
            </label>
          </div>
          <label>
            Reporting period
            <textarea
              value={value.period_definition}
              onChange={(e) => setValue({ ...value, period_definition: e.target.value })}
              rows={2}
              maxLength={1000}
              placeholder="For example: current and previous each cover 30 complete days; customers are active customers at the start of the period."
              disabled={!canWrite}
            />
          </label>
          <div className="dictionary-columns">
            {dataset.columns.map((column) => (
              <label key={column.name}>
                {column.label || column.name}
                <input
                  value={value.columns[column.name] || ''}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      columns: { ...value.columns, [column.name]: e.target.value },
                    })
                  }
                  placeholder="Field meaning, units, and caveats"
                  disabled={!canWrite}
                />
              </label>
            ))}
          </div>
          <label>
            Business metrics
            <textarea
              value={metrics}
              onChange={(e) => setMetrics(e.target.value)}
              rows={3}
              placeholder={
                'Gross margin = (revenue - cost) / revenue\nChurn rate = lost customers / customers at period start'
              }
              disabled={!canWrite}
            />
            <small>One metric per line: name = calculation.</small>
          </label>
          {canWrite && (
            <button className="button secondary" disabled={busy}>
              Save business definitions
            </button>
          )}
        </form>
      )}
    </details>
  );
}
