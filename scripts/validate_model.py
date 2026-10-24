"""Opt-in real model acceptance on synthetic fixtures; cloud mode makes billable calls."""
import argparse
import json
from pathlib import Path
import tempfile
import time

from app.config import Settings
from app.database import Database
from app.engine import Engine
from app.evaluation import score_case
from app.seed import seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=['ollama', 'live'])
    parser.add_argument('--model')
    parser.add_argument('--decision-mode', choices=['llm', 'openai_decisions'], default='llm')
    parser.add_argument('--case', choices=['sql', 'rag', 'mixed', 'all'], default='all')
    parser.add_argument('--output', type=Path, default=Path('output/model-acceptance.json'))
    args = parser.parse_args()
    settings = Settings.from_env()
    model = args.model or (settings.openai_model if args.provider == 'live' else settings.ollama_model)
    if not model:
        parser.error('Set --model or the corresponding model in .env.')
    settings.orchestration = 'langgraph'
    settings.max_model_calls = min(settings.max_model_calls, 14)
    settings.analysis_timeout = min(settings.analysis_timeout, 240)
    # Keep fixtures self-contained. Real semantic service contracts are tested separately.
    settings.retrieval_mode = 'lexical'
    settings.embedding_provider = 'lexical'
    records = []
    with tempfile.TemporaryDirectory(prefix='market-lens-acceptance-') as directory:
        db = Database(Path(directory)/'fixtures.sqlite3')
        db.initialize()
        seed(db)
        stock = db.import_csv('Acceptance stock', 'sku,stock_units,min_required\nA,4,8\nB,12,5\n')
        policy = db.add_document('Acceptance service policy',
            'Service recovery policy. A service credit requires an unresolved support ticket open for at least 7 days. '
            'The monthly credit cap is SGD 25 per customer. A team lead must approve the credit before it is issued. '
            'No automatic credit or payment is authorized by this policy.', 'Synthetic acceptance SOP')
        cases = {
            'sql': {'question': 'Use only the selected inventory table. Return the sku column for products whose stock_units are below min_required, and recommend the next review step. Do not infer demand or prices.',
                    'dataset_id': stock['id'], 'document_ids': [], 'expected_route': 'sql',
                    'expected_sql': f'SELECT sku FROM "{stock["table_name"]}" WHERE stock_units < min_required'},
            'rag': {'question': 'According to the selected service recovery policy, what conditions and approval are required for a credit, and what is the monthly cap? Recommend how to check eligibility without issuing a credit.',
                    'dataset_id': None, 'document_ids': [policy['id']], 'expected_route': 'rag', 'expected_document_ids': [policy['id']]},
            'mixed': {'question': 'Calculate previous and current customer churn rates, relate them cautiously to support and onboarding evidence, and recommend a small reversible experiment. Distinguish causal hypotheses from observed facts.',
                      'dataset_id': 'demo_retention', 'document_ids': [], 'expected_route': 'mixed'},
        }
        for name, case in cases.items():
            if args.case not in ('all', name):
                continue
            start = time.perf_counter()
            try:
                request = {k: case[k] for k in ('question', 'dataset_id', 'document_ids')}
                request.update(agent_mode=args.provider, decision_mode=args.decision_mode, model=model, routing_mode='auto')
                result = Engine(db, settings).analyze(request)
                scores, route = score_case(case, result, db)
                provider = result['decision']['provider']
                item = {'case': name, 'completed': True, 'scores': scores, 'actual_route': route,
                        'decision_provider': provider,
                        'requested_decision_exercised': args.decision_mode != 'openai_decisions' or provider == 'openai_decisions',
                        'result': result}
            except Exception as error:
                item = {'case': name, 'completed': False, 'error_type': type(error).__name__}
            item['elapsed_seconds'] = round(time.perf_counter()-start, 2)
            records.append(item)
            print(json.dumps({k: v for k, v in item.items() if k != 'result'}, ensure_ascii=False), flush=True)
    report = {'provider': args.provider, 'model': model, 'decision_mode': args.decision_mode, 'fixture_kind': 'synthetic engineering acceptance, not independent business validation', 'cases': records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    raise SystemExit(0 if all(r['completed'] and r.get('requested_decision_exercised', False) for r in records) else 1)


if __name__ == '__main__':
    main()
