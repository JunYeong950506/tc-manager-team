"""Save and resume TC authoring candidates without creating Zephyr test cases."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tc_candidates as candidates
import tc_draft
import tc_library
import tc_registry
from tc_shared import Remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='.tc-manager/library.json')
    parser.add_argument('--site', required=True)
    parser.add_argument('--project', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('--story')
    listing.add_argument('--status', choices=['registered','needs_evidence','awaiting_implementation'])
    listing.add_argument('--offset',type=int,default=0)
    get = sub.add_parser('get'); get.add_argument('id'); get.add_argument('--offset',type=int,default=0)
    save = sub.add_parser('save'); save.add_argument('file')
    save.add_argument('--prepared'); save.add_argument('--review')
    parser.add_argument('--output')
    args = parser.parse_args()
    db, namespace = tc_library.settings(args.config,args.site,args.project)
    if args.command == 'save':
        payload = tc_draft.load(args.file)
        if 'namespace' in payload and payload['namespace'] != namespace:
            raise ValueError('선택한 프로젝트와 초안 프로젝트가 다릅니다')
        if payload.get('status') == 'registered':
            if not args.prepared or not args.review:
                raise ValueError('등록 완료에는 --prepared 및 --review가 필요합니다')
            folder,state,draft,target = tc_draft.context(args.prepared)
            review = tc_draft.load(args.review)
            if state['status'] != 'verified' or draft != payload['draft'] or review.get('approved') is not True or review.get('draft_hash') != tc_draft.digest(draft):
                raise ValueError('최종 초안의 사람 검토와 원격 재조회를 확인하세요')
            payload['registration'] = dict(state,human_reviewed_draft_hash=review['draft_hash'])
            payload['target'] = target
        op = 'candidates.save'
    elif args.command == 'get':
        op,payload='candidates.get',dict(id=args.id,offset=args.offset)
    else:
        op,payload='candidates.list',dict(offset=args.offset)
        if args.story: payload['story_key']=args.story
        if args.status: payload['status']=args.status
    if isinstance(db, Remote):
        result=db.rpc(op,payload)
    else:
        payload['namespace']=namespace
        with closing(tc_registry.connect(db,create=True)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE' if op in candidates.WRITE_OPS else 'BEGIN')
            if op in candidates.WRITE_OPS: candidates.initialize(connection)
            if op not in candidates.WRITE_OPS and not connection.execute("SELECT 1 FROM sqlite_master WHERE name='tc_authoring_candidates'").fetchone():
                if op == 'candidates.get': raise ValueError('저장된 초안이 없습니다')
                result=dict(items=[],total=0)
            else:
                result=candidates.execute(connection,op,payload,dict(id='local-user',role='admin',namespaces=[namespace]),datetime.now(timezone.utc).isoformat())
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: Path(args.output).write_text(output+'\n',encoding='utf-8')
    print(output)

if __name__ == '__main__':
    try:
        main()
    except (ValueError,OSError,KeyError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
