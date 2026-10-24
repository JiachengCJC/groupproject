import type { Config, LocalModels, LocalProfile, LocalRouting, NamedLocalProfile } from '../types';
import { localProfileIssue, localProfileLabels, retrievalModelLabel } from '../utils';
import { Notice } from './ui';

const profiles: NamedLocalProfile[] = ['fast', 'quality', 'auto'];

export default function LocalProfileSettings({
  config,
  models,
  value,
  onChange,
  disabled = false,
}: {
  config: Config;
  models: LocalModels | null;
  value: LocalProfile;
  onChange: (value: LocalProfile) => void;
  disabled?: boolean;
}) {
  const definition = value === 'custom' ? undefined : config.local_profiles?.[value];
  const issue = localProfileIssue(value, config, models);
  return (
    <div className="local-profile-settings">
      <label>
        Local run mode
        <select
          value={value}
          onChange={(event) => onChange(event.target.value as LocalProfile)}
          disabled={disabled}
        >
          {profiles.map((profile) => (
            <option key={profile} value={profile} disabled={!config.local_profiles?.[profile]}>
              {config.local_profiles?.[profile]?.label || localProfileLabels[profile]}
            </option>
          ))}
          <option value="custom">Custom local · Choose a model</option>
        </select>
      </label>
      {definition ? (
        <div className="local-profile-card">
          <b>{definition.label}</b>
          <p>{definition.description}</p>
          <dl>
            <dt>Planning</dt>
            <dd>{definition.models.planner}</dd>
            <dt>{value === 'auto' ? 'Analysis, when needed' : 'Analysis and decision'}</dt>
            <dd>{definition.models.analysis}</dd>
            <dt>Image extraction</dt>
            <dd>{config.ollama_vision_model || 'Local OCR; no vision model configured'}</dd>
            <dt>Document retrieval</dt>
            <dd>{retrievalModelLabel(config)}</dd>
          </dl>
          <p className="help">
            Model recommendations stay local. Image extraction is selected when uploading a source.
            Tasks run in sequence to limit memory use.
          </p>
        </div>
      ) : value === 'custom' ? (
        <p className="help">
          Choose one installed model for the analysis. The decision method and retrieval
          configuration determine whether cloud services are used.
        </p>
      ) : null}
      {issue && <Notice error>{issue}</Notice>}
    </div>
  );
}

export function LocalRoutingSummary({ routing }: { routing: LocalRouting }) {
  return (
    <section className="local-routing-summary" aria-label="Local model routing">
      <div className="local-routing-heading">
        <b>{localProfileLabels[routing.profile]}</b>
        <span className="badge">Local model routing</span>
      </div>
      <p>{routing.reason}</p>
      <dl>
        <dt>Planning model</dt>
        <dd>{routing.planner_model}</dd>
        <dt>Selected analysis model</dt>
        <dd>{routing.analysis_model}</dd>
      </dl>
      {!!Object.keys(routing.stages).length && (
        <details>
          <summary>Models used in this analysis</summary>
          <dl>
            {Object.entries(routing.stages).map(([stage, model]) => (
              <div key={stage}>
                <dt>{stage.replace(/([a-z])([A-Z])/g, '$1 $2')}</dt>
                <dd>{model}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </section>
  );
}
