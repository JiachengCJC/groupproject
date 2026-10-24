# Market Lens

Analyze business data and documents with local Ollama models or OpenAI, check the evidence, and export reviewed reports.

**Upload sources → Ask a question → Check the evidence → Approve → Export**

## 1. Start the application

Install **Git**, **Python 3**, and **Docker Desktop**. Start Docker before continuing. Docker installs the application and supporting services.

Replace `<repository-url>` with this repository's GitHub clone URL:

```bash
git clone <repository-url> market-lens
cd market-lens
python3 scripts/configure.py
docker compose up --build -d --wait
```

On Windows, use `python` or `py` if `python3` is unavailable. The first build needs internet access and may take several minutes.

### Register the first account

1. Open **http://127.0.0.1:8000**. If `.env` sets `PORT=8002`, use **http://127.0.0.1:8002** instead.
2. On **Create your workspace**, enter your name, email, and a password of **12–128 characters**.
3. Open the `.env` file created by `scripts/configure.py`. Copy only the value after `SETUP_TOKEN=` into **Setup token**.
4. Click **Create workspace**. This creates the first **Administrator** account and signs you in.

The setup token is used only for initial registration. For later visits, use your email and password and click **Sign in to workspace**. If you already see the sign-in screen, an administrator account exists; contact that administrator for access or a password reset.

Each installation has its own accounts and data. Someone cloning this repository creates their own `.env` and first account. API keys, setup tokens, accounts, and uploaded data are not included in the repository.

### Add teammates to an existing workspace

1. Sign in as an administrator and open **Workspace → Add member**.
2. Enter the teammate's name, email, role, and an initial password of **12–128 characters**, then click **Add member**.
3. Share the workspace URL and their login details privately. They sign in with that email and password; no setup token is needed.

Choose **Analyst** to upload sources and run analyses, **Reviewer** to approve reports, **Viewer** to browse, or **Administrator** to manage the workspace. There is no public sign-up or automatic invitation email. Administrators can use **Workspace → Reset password** when needed.

The default address works only on the computer running Docker. To share one workspace across computers, use a shared deployment URL; see [HTTPS deployment](docs/OPERATIONS.md#https-cloud-deployment). Running a separate clone creates a separate workspace.

**Try it immediately:** select a built-in demo in **Analysis workspace** and click **Start analysis**. The default rule baseline uses synthetic demo data and needs no model or API key. For your own questions, configure an option below.

## 2. Choose local or cloud models

### Option A — Local Ollama

Install and start Ollama on your computer, then download:

```bash
ollama pull qwen3.5:4b-q4_K_M
ollama pull qwen3.5:9b-q4_K_M
ollama pull bge-m3
```

Edit the matching entries in `.env`, keeping the generated passwords and setup token:

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_FAST_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_QUALITY_MODEL=qwen3.5:9b-q4_K_M
OLLAMA_VISION_MODEL=qwen3.5:4b-q4_K_M
RETRIEVAL_MODE=hybrid
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3
```

Apply the settings:

```bash
docker compose up -d --wait
```

In **Analysis workspace → Model and analysis settings**, set **Analysis model** to **Ollama · Local model**, then choose a **Local run mode**:

| Mode | What it does |
| --- | --- |
| **Local Fast** | Uses 4B throughout. Start here for everyday questions. |
| **Local Quality** | Uses 9B throughout. Needs more time and memory. |
| **Local Auto** | Plans with 4B, then selects 4B or 9B and records the reason. |
| **Custom local** | Lets you select an installed model manually. |

The three named modes use local model recommendations. BGE-M3 handles document retrieval; 4B handles images when you select **Local vision model** during upload. After changing embedding settings, click **Knowledge library → Update semantic index**.

On a 16 GB Mac, start with Fast and one worker. With native Docker on Linux, ensure the host Ollama service is reachable from containers. See [operations and troubleshooting](docs/OPERATIONS.md) for connection and memory settings.

### Option B — OpenAI cloud

Edit these entries in `.env` using your own API key and a Responses model available to your account:

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=your-responses-model-id
OPENAI_DECISION_MODEL=gpt-6-luna
LOCAL_ONLY=false
```

Run `docker compose up -d --wait`, then select **Cloud model** under **Analysis model**. Choose **Model recommendation**, or **OpenAI Decisions** to compare candidate actions. Both use the same OpenAI key; no TypeSafe key is needed. Cloud calls send selected evidence to OpenAI and incur API charges.

A fresh installation uses keyword retrieval and does not require Ollama. If you previously enabled local embeddings and want to stop using Ollama, set both `RETRIEVAL_MODE` and `EMBEDDING_PROVIDER` to `lexical` before applying the settings.

For a deployment-wide offline boundary, set `LOCAL_ONLY=true`. This also disables cloud models, OpenAI Decisions, and online source synchronization.

## 3. Run your first analysis

1. Open **Knowledge library**, upload a spreadsheet or document, and wait for processing to finish.
2. Open **Analysis workspace** and select the dataset and reference documents.
3. Enter a question, such as: *Which products are below minimum stock, and what does our replenishment policy require?*
4. Choose your model settings and click **Start analysis**.
5. Review **Decision overview**, **Specialist findings**, and **Source evidence**. Model output can contain mistakes.
6. Add review notes and click **Approve this version** to unlock **PDF**, **HTML**, and **JSON** exports.

Use **Evaluation** to compare results against answers your team has checked.

## 4. Everyday commands

Run these from the repository folder:

| Task | Command |
| --- | --- |
| Start again or apply `.env` changes | `docker compose up -d --wait` |
| Rebuild after updating the code | `docker compose up --build -d --wait` |
| Check service status | `docker compose ps` |
| View logs | `docker compose logs -f market-lens worker` |
| Stop while keeping saved data | `docker compose down` |

If the port is occupied, change `PORT` in `.env` and apply the settings. Avoid `docker compose down -v` unless you intend to delete persistent data. Models and API keys are configured separately on each computer.

When pushing changes to GitHub, keep `.env` and local data private. The ignore rules exclude credentials, databases, backups, dependencies, and generated reports. Commit `.env.example`, `requirements-lock.txt`, and `frontend/package-lock.json` so others can reproduce the setup.

## More details

- [Operations, backups, and troubleshooting](docs/OPERATIONS.md)
- [Test results and known limitations](docs/ACCEPTANCE.md)
- [Run local model acceptance checks](scripts/LOCAL_ACCEPTANCE.md)
- [Architecture and refactor plan](docs/REFACTOR_PLAN.md)
