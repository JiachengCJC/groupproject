import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import type { Connector, Dataset, Document, QueryResult } from '../types';
import { date } from '../utils';
import DictionaryEditor from '../components/DictionaryEditor';
import QueryChart from '../components/QueryChart';
import { TaskProgress } from '../components/Tasks';
import {
  DataTable,
  Empty,
  Icon,
  Loading,
  Modal,
  Notice,
  PageHeading,
  Status,
} from '../components/ui';
interface UploadResult {
  name: string;
  status: string;
  message: string;
  warnings?: string[];
  taskId?: string;
}
export default function Knowledge() {
  const { sources, config, models, refreshSources, notify } = useWorkspace();
  const { canWrite } = usePermissions();
  const [indexTask, setIndexTask] = useState('');
  const [scope, setScope] = useState('');
  const [vision, setVision] = useState('none');
  const [files, setFiles] = useState<File[]>([]);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [detail, setDetail] = useState<{
    title: string;
    document?: Document;
    dataset?: Dataset;
    preview?: QueryResult;
  } | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  async function preview(id: string, kind: string) {
    setDetailBusy(true);
    try {
      if (kind === 'dataset') {
        const item = await api<{ dataset: Dataset; preview: QueryResult }>(
          '/datasets/' + encodeURIComponent(id),
        );
        setDetail({ title: item.dataset.name, ...item });
      } else {
        const document = await api<Document>('/documents/' + encodeURIComponent(id));
        setDetail({ title: document.title, document });
      }
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setDetailBusy(false);
    }
  }
  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!files.length) return;
    if (files.length > 64) {
      notify('Upload no more than 64 files at a time.', true);
      return;
    }
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setUploads(
      files.map((file) => ({ name: file.name, status: 'queued', message: 'Waiting to process' })),
    );
    for (const [index, file] of files.entries()) {
      const update = (result: UploadResult) =>
        setUploads((previous) => previous.map((item, i) => (i === index ? result : item)));
      if (file.size > (config.max_file_bytes || 25_000_000)) {
        update({
          name: file.name,
          status: 'failed',
          message: 'This file exceeds the size limit. Split it into smaller files and try again.',
        });
        continue;
      }
      update({ name: file.name, status: 'running', message: 'Uploading and processing…' });
      const body = new FormData();
      body.append('file', file);
      body.append('dataset_id', scope);
      body.append('category', String(form.get('category')));
      body.append('vision_provider', vision);
      body.append('background', 'true');
      if (vision === 'ollama') body.append('vision_model', String(form.get('vision_model') || ''));
      try {
        const result = await api<{
          status: string;
          task_id?: string;
          documents?: unknown[];
          datasets?: unknown[];
          warnings?: string[];
          taskId?: string;
        }>('/uploads', body);
        update({
          name: file.name,
          status: result.task_id ? 'queued' : 'completed',
          taskId: result.task_id,
          message: result.task_id
            ? 'Uploaded. Processing in the background.'
            : result.status === 'duplicate'
              ? 'This file already exists. The original record has been preserved.'
              : `Processed · ${result.documents?.length || 0} documents / ${result.datasets?.length || 0} datasets`,
          warnings: result.warnings,
        });
      } catch (err) {
        update({ name: file.name, status: 'failed', message: errorMessage(err) });
      }
    }
    try {
      await refreshSources();
    } catch (err) {
      notify(errorMessage(err), true);
    }
    setBusy(false);
  }
  async function addDocument(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const element = e.currentTarget;
    const form = new FormData(element);
    setBusy(true);
    try {
      await api('/documents', {
        title: String(form.get('title')),
        content: String(form.get('content')),
        source: String(form.get('source') || 'Team sources'),
        dataset_id: String(form.get('dataset_id') || '') || null,
      });
      element.reset();
      await refreshSources();
      notify('Document added to the knowledge library.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  const filteredDatasets = sources.datasets.filter((d) =>
    d.name.toLowerCase().includes(filter.toLowerCase()),
  );
  const filteredDocuments = sources.documents.filter((d) =>
    d.title.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="THE EVIDENCE BEHIND THE ANSWER"
        title="Your knowledge library."
        description="Connect data, documents, and business context to ground your analyses in evidence."
        action={
          <div className="inline-actions">
            {canWrite && (
              <button
                className="button secondary"
                onClick={async () => {
                  try {
                    const task = await api<{ task_id: string }>('/documents/index', {});
                    setIndexTask(task.task_id);
                  } catch (err) {
                    notify(errorMessage(err), true);
                  }
                }}
              >
                <Icon name="refresh" size={16} />
                Update semantic index
              </button>
            )}
            <a className="button secondary" href="/api/sample.csv" download>
              <Icon name="download" size={17} />
              Sample CSV
            </a>
          </div>
        }
      />
      {indexTask && (
        <TaskProgress
          taskId={indexTask}
          onComplete={() => {
            void refreshSources().catch((error) => notify(errorMessage(error), true));
          }}
        />
      )}
      <div className="knowledge-grid">
        <section className="panel source-library">
          <div className="section-heading">
            <h2>Connected sources</h2>
            <span className="help">
              {sources.datasets.length} datasets · {sources.documents.length} documents
            </span>
          </div>
          <label className="search-field">
            <Icon name="search" size={18} />
            <input
              aria-label="Search sources"
              placeholder="Search datasets or documents…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          {detailBusy && <Loading label="Opening source…" />}
          <h3 className="source-section-label">Dataset</h3>
          {filteredDatasets.map((dataset) => (
            <button
              key={dataset.id}
              className="source-row"
              onClick={() => void preview(dataset.id, 'dataset')}
            >
              <span className="source-icon">
                <Icon name="database" />
              </span>
              <span>
                <b>{dataset.name}</b>
                <small>
                  {dataset.row_count.toLocaleString('en-SG')}{' '}
                  {dataset.row_count === 1 ? 'row' : 'rows'} · {dataset.columns.length}{' '}
                  {dataset.columns.length === 1 ? 'column' : 'columns'}
                  {dataset.synthetic ? ' · Synthetic data' : ''}
                </small>
              </span>
              <Icon name="arrow" size={17} />
            </button>
          ))}
          {!filteredDatasets.length && (
            <p className="empty-inline">
              {filter
                ? 'No matching datasets.'
                : 'Upload a spreadsheet to make its data available for analysis here.'}
            </p>
          )}
          <h3 className="source-section-label">Documents</h3>
          {filteredDocuments.map((document) => (
            <button
              key={document.id}
              className="source-row"
              onClick={() => void preview(document.id, 'document')}
            >
              <span className="source-icon document-icon">
                <Icon name="file" />
              </span>
              <span>
                <b>{document.title}</b>
                <small>
                  {document.chunk_count || 0} {document.chunk_count === 1 ? 'excerpt' : 'excerpts'}{' '}
                  · {document.category || 'General sources'}
                  {document.synthetic ? ' · Synthetic document' : ''}
                </small>
              </span>
              <Icon name="arrow" size={17} />
            </button>
          ))}
          {!filteredDocuments.length && (
            <p className="empty-inline">
              {filter
                ? 'No matching documents.'
                : 'Upload reports, policies, or research to build your team’s knowledge library.'}
            </p>
          )}
        </section>
        <div className="import-stack">
          {canWrite ? (
            <>
              <form className="panel upload-form" onSubmit={upload}>
                <p className="eyebrow">DOCUMENTS & DATA</p>
                <h2>Upload your sources</h2>
                <p className="help">
                  Supports PDF, Word, Excel, images, text, and archives. Maximum file size:{' '}
                  {Math.round((config.max_file_bytes || 25_000_000) / 1_000_000)} MB。
                </p>
                <label className="upload-drop">
                  <Icon name="upload" size={25} />
                  <b>
                    {files.length
                      ? `${files.length} files selected`
                      : 'Choose files to start building your library'}
                  </b>
                  <span>Select multiple files at once</span>
                  <input
                    type="file"
                    multiple
                    required
                    aria-label="Choose files to upload"
                    disabled={busy}
                    onChange={(e) => setFiles(Array.from(e.target.files || []))}
                  />
                </label>
                <label>
                  Source scope
                  <select value={scope} onChange={(e) => setScope(e.target.value)} disabled={busy}>
                    <option value="">Shared sources / Standalone documents</option>
                    {sources.datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>
                        {dataset.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Source category
                  <select name="category" disabled={busy}>
                    {[
                      'General',
                      'Customer research',
                      'Competitive intelligence',
                      'Product & quality',
                      'Market research',
                      'Internal policy',
                      'Operations',
                    ].map((value, i) => (
                      <option value={value} key={value}>
                        {
                          [
                            'General sources',
                            'Customer research',
                            'Competitive intelligence',
                            'Product and quality',
                            'Market research',
                            'Internal policy',
                            'Operations',
                          ][i]
                        }
                      </option>
                    ))}
                  </select>
                </label>
                <details className="form-details">
                  <summary>Image and scanned document processing</summary>
                  <label>
                    Extraction method
                    <select
                      value={vision}
                      onChange={(e) => setVision(e.target.value)}
                      disabled={busy}
                    >
                      <option value="none">Local OCR · No model</option>
                      <option value="ollama">Local vision model</option>
                      <option
                        value="live"
                        disabled={config.local_only || !config.live_agents_available}
                      >
                        Cloud vision model
                      </option>
                    </select>
                  </label>
                  {vision === 'ollama' && (
                    <label>
                      Vision model
                      <select
                        name="vision_model"
                        required
                        defaultValue={config.ollama_vision_model || ''}
                      >
                        <option value="">Select a model that supports images</option>
                        {models?.models.map((m) => (
                          <option key={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  <p className="help">
                    {vision === 'live'
                      ? 'Images are sent to the configured cloud model for extraction.'
                      : vision === 'ollama'
                        ? 'Choose a model with image understanding. Text-only models are not suitable.'
                        : 'Text recognition runs locally. Complex charts may need human verification.'}
                  </p>
                </details>
                <button className="button primary full" disabled={busy}>
                  {busy ? <span className="spinner" /> : <Icon name="plus" size={17} />}{' '}
                  {busy ? 'Processing sources…' : 'Upload and process'}
                </button>
                {uploads.length > 0 && (
                  <div className="upload-results" aria-live="polite">
                    {uploads.map((item, i) => (
                      <div key={i} className={'upload-result ' + item.status}>
                        <div>
                          <b>{item.name}</b>
                          <Status status={item.status} />
                        </div>
                        <p>{item.message}</p>
                        {item.taskId && (
                          <TaskProgress
                            taskId={item.taskId}
                            onComplete={(task) => {
                              setUploads((previous) =>
                                previous.map((entry, n) =>
                                  n === i
                                    ? {
                                        ...entry,
                                        status: task.status,
                                        message:
                                          task.error ||
                                          (task.status === 'completed'
                                            ? 'Background parsing completed.'
                                            : 'Task finished.'),
                                        warnings: (task.result as { warnings?: string[] } | null)
                                          ?.warnings,
                                      }
                                    : entry,
                                ),
                              );
                              void refreshSources().catch((error) =>
                                notify(errorMessage(error), true),
                              );
                            }}
                          />
                        )}
                        {item.warnings?.map((warning, n) => (
                          <p className="help" key={n}>
                            {warning}
                          </p>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </form>
              <details className="panel manual-document">
                <summary>
                  <Icon name="file" size={19} />
                  Add document text directly
                </summary>
                <form onSubmit={addDocument}>
                  <label>
                    Title
                    <input name="title" required maxLength={160} />
                  </label>
                  <label>
                    Source or attribution
                    <input
                      name="source"
                      maxLength={500}
                      placeholder="For example: Internal policy · October 2026"
                    />
                  </label>
                  <label>
                    Linked dataset
                    <select name="dataset_id">
                      <option value="">Shared sources</option>
                      {sources.datasets.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Document content
                    <textarea name="content" required rows={6} maxLength={2_000_000} />
                  </label>
                  <button className="button secondary" disabled={busy}>
                    Add document
                  </button>
                </form>
              </details>
            </>
          ) : (
            <Notice>
              Your role can view sources. Uploading and syncing require an analyst or administrator
              role.
            </Notice>
          )}
        </div>
      </div>
      <Connectors />
      {detail && (
        <Modal title={detail.title} onClose={() => setDetail(null)}>
          {detail.document ? (
            <>
              <p className="help">
                {detail.document.source} · {detail.document.extraction_method || 'Text'}
              </p>
              {detail.document.warnings?.map((warning, i) => (
                <Notice key={i}>{warning}</Notice>
              ))}
              {detail.document.file_id && (
                <a
                  className="button secondary small"
                  href={'/api/files/' + encodeURIComponent(detail.document.file_id)}
                  download
                >
                  Download original file
                </a>
              )}
              <blockquote className="document-content">{detail.document.content}</blockquote>
            </>
          ) : (
            <>
              {detail.preview && (
                <>
                  <DataTable data={detail.preview} />
                  <QueryChart data={detail.preview} />
                </>
              )}
              <h3>Data structure</h3>
              <div className="schema-chips">
                {detail.dataset?.columns.map((column) => (
                  <span key={column.name}>
                    {column.label || column.name}
                    <small>{column.type}</small>
                  </span>
                ))}
              </div>
              {detail.dataset && <DictionaryEditor dataset={detail.dataset} />}
            </>
          )}
        </Modal>
      )}
    </>
  );
}
function Connectors() {
  const { notify, refreshSources, config } = useWorkspace();
  const { canWrite } = usePermissions();
  const [items, setItems] = useState<Connector[]>([]);
  const [error, setError] = useState('');
  const [kind, setKind] = useState('web');
  const [busy, setBusy] = useState('');
  const [tasks, setTasks] = useState<Record<string, string>>({});
  async function load() {
    try {
      const result = await api<Connector[]>('/connectors');
      setItems(result);
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const element = e.currentTarget;
    const form = new FormData(element);
    setBusy('create');
    try {
      await api('/connectors', {
        name: String(form.get('name')),
        kind,
        ...(kind === 'kaggle'
          ? { dataset: String(form.get('location')) }
          : { url: String(form.get('location')) }),
      });
      element.reset();
      await load();
      notify('Data connection saved. Select Sync to import its sources.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  async function sync(id: string) {
    setBusy(id);
    try {
      const result = await api<{ status?: string; task_id?: string }>(
        '/connectors/' + encodeURIComponent(id) + '/sync',
        {},
      );
      if (result.task_id) setTasks((previous) => ({ ...previous, [id]: result.task_id! }));
      await load();
      await refreshSources();
      notify(
        result?.task_id || result?.status === 'queued'
          ? 'Sync queued. Refresh later to view the results.'
          : 'Source sync completed.',
      );
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  return (
    <section className="connections-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CONNECTED SOURCES</p>
          <h2>Keep external sources up to date.</h2>
        </div>
        <button className="button secondary small" onClick={() => void load()}>
          <Icon name="refresh" size={15} />
          Refresh
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      {config.local_only && (
        <Notice>
          External source sync follows local mode and server network policies. Existing sources
          remain available for analysis.
        </Notice>
      )}
      <div className="connector-grid">
        <div className="panel connector-list">
          {items.length ? (
            items.map((item) => (
              <article key={item.id} className="connector-row">
                <span className="source-icon">
                  <Icon name={item.kind === 'kaggle' ? 'database' : 'globe'} />
                </span>
                <div>
                  <b>{item.name}</b>
                  <small>
                    {item.kind.toUpperCase()} · {item.url || item.dataset}
                  </small>
                  {item.last_sync && <small>Last synced {date(item.last_sync)}</small>}
                  {item.last_error && <p className="error-text">{item.last_error}</p>}
                  {tasks[item.id] && (
                    <TaskProgress
                      taskId={tasks[item.id]}
                      onComplete={() => {
                        void load();
                        void refreshSources().catch((error) => notify(errorMessage(error), true));
                      }}
                    />
                  )}
                </div>
                {canWrite && (
                  <button
                    className="button secondary small"
                    disabled={!!busy}
                    onClick={() => void sync(item.id)}
                  >
                    {busy === item.id ? 'Syncing…' : 'Sync'}
                  </button>
                )}
              </article>
            ))
          ) : (
            <Empty title="Connect your information sources" icon="link">
              Add a webpage, RSS feed, or Kaggle dataset with source attribution and sync history.
            </Empty>
          )}
        </div>
        {canWrite && (
          <form className="panel connector-form" onSubmit={create}>
            <h3>Add data connection</h3>
            <label>
              Name
              <input
                name="name"
                required
                maxLength={120}
                placeholder="For example: Industry news"
              />
            </label>
            <label>
              Source type
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="web">Webpage</option>
                <option value="rss">RSS / Atom feed</option>
                <option value="kaggle">Kaggle dataset</option>
              </select>
            </label>
            <label>
              {kind === 'kaggle' ? 'Dataset identifier' : 'Source URL'}
              <input
                key={kind}
                name="location"
                type={kind === 'kaggle' ? 'text' : 'url'}
                required
                placeholder={kind === 'kaggle' ? 'owner/dataset-name' : 'https://example.com/news'}
              />
            </label>
            <p className="help">
              Webpages must meet the server’s source allowlist policy. Restricted Kaggle datasets
              require access credentials.
            </p>
            <button className="button secondary" disabled={!!busy}>
              <Icon name="plus" size={16} />
              Save connection
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
