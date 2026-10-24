import { useState } from 'react';
import type { QueryResult } from '../types';
import { fmt } from '../utils';
export default function QueryChart({ data }: { data: Partial<QueryResult> }) {
  const rows = data.rows || [];
  const columns = data.columns || Object.keys(rows[0] || {});
  const numeric = columns.filter(
    (column) =>
      rows.some((row) => typeof row[column] === 'number') &&
      rows.every((row) => row[column] == null || typeof row[column] === 'number'),
  );
  const [chosen, setChosen] = useState('');
  if (!rows.length || !numeric.length || rows.length > 200) return null;
  const metric = numeric.includes(chosen) ? chosen : numeric[0];
  const labelColumn = columns.find((column) => !numeric.includes(column));
  const shown = rows.slice(0, 12);
  const maximum = Math.max(...shown.map((row) => Math.abs(Number(row[metric]) || 0)), 1);
  const negative = shown.some((row) => Number(row[metric]) < 0);
  return (
    <details className="query-chart">
      <summary>View query chart</summary>
      <label>
        Numeric column
        <select value={metric} onChange={(e) => setChosen(e.target.value)}>
          {numeric.map((column) => (
            <option key={column}>{column}</option>
          ))}
        </select>
      </label>
      <div
        className="chart-rows"
        role="img"
        aria-label={`${metric} query bar chart showing the first ${shown.length} rows`}
      >
        {shown.map((row, i) => {
          const value = Number(row[metric]) || 0;
          const width = (Math.abs(value) / maximum) * (negative ? 50 : 100);
          return (
            <div key={i} className="chart-row">
              <span title={labelColumn ? String(row[labelColumn]) : String(i + 1)}>
                {labelColumn ? String(row[labelColumn] ?? 'NULL') : `Row ${i + 1} rows`}
              </span>
              <div className={'chart-track' + (negative ? ' has-negative' : '')}>
                <i
                  className={value < 0 ? 'negative' : ''}
                  style={{
                    width: `${width}%`,
                    left: `${negative ? (value < 0 ? 50 - width : 50) : 0}%`,
                  }}
                />
              </div>
              <b>{row[metric] == null ? 'NULL' : fmt(row[metric], 3)}</b>
            </div>
          );
        })}
      </div>
      <p className="help">
        {rows.length > 12
          ? 'The chart shows the first 12 rows. See the table for complete results.'
          : 'Values come directly from the query results.'}{' '}
        Null values are not plotted.
      </p>
    </details>
  );
}
