#!/usr/bin/env python3
"""Durable incremental queue; the scheduled Agent performs Feishu CLI operations."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

import notehelper_client as cloud


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise cloud.ClientError(message)


def save(path, value):
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(str(temp), str(path))


@contextmanager
def locked(root):
    path = root / '.sync.lock'
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise cloud.ClientError('任务正在运行或旧锁待核对；不要并发重建，确认原进程已结束后再恢复。')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def configured_key(config):
    value = json.loads(Path(config).read_text(encoding='utf-8'))
    key = value.get('api_key') if isinstance(value, dict) else None
    require(isinstance(key, str) and bool(key.strip()) and not any(c.isspace() for c in key),
            '任务指定的持久配置没有有效密钥；不回退到其它用户或临时环境变量。')
    return key


def metadata(config):
    key = configured_key(config)
    result, after = {}, 0
    for _ in range(100):
        page = cloud.search(key, '', 50, after, False)
        before = len(result)
        for article in page['articles']:
            result[article['id']] = article
        if page['next_after'] is None:
            return result
        require(len(result) > before, '分页没有取得新 ID，未更新同步记录；先核对官方分页行为。')
        after = page['next_after']
    raise cloud.ClientError('元信息超过本轮分页上限，未更新基线或漏记完成；请由 Agent 核对后扩展分页。')


def doc_token(url):
    parts = urlsplit(url)
    require(parts.scheme == 'https' and not parts.username and not parts.password,
            '需要实际飞书 HTTPS 文档 URL。')
    require(any((parts.hostname or '').endswith('.' + domain) or parts.hostname == domain
                for domain in ('feishu.cn', 'larksuite.com', 'larkoffice.com')),
            '文档 URL 必须来自飞书或 Lark。')
    match = re.fullmatch(r'/docx/([A-Za-z0-9]+)', parts.path.rstrip('/'))
    require(bool(match), '请记录实际创建的 /docx/ 文档 URL，不用授权链接。')
    return match.group(1)


def initialize(root, args):
    require(args.user_tested or getattr(args, 'manual', False), '定时初始化前需要用户验收；首次手动测试使用 --manual。')
    require(not root.exists(), '任务目录已存在；请读取原 job.json 和 status 续接，不重新初始化。')
    require(Path(args.config).is_file(), '请使用已经验证的本机密钥配置文件。')
    if args.test_doc_url:
        doc_token(args.test_doc_url)
    board = json.loads(Path(args.board).read_text(encoding='utf-8')) if getattr(args, 'board', None) else None
    if board:
        require(bool(board.get('base_token')) and all(n in board.get('tables', {}) for n in
                ('选题池', '视频剪辑', '作品发布库', '作品数据快照')), '模板映射不完整。')
    baseline = metadata(args.config)
    require(getattr(args, 'manual', False) or args.test_note_id in baseline, '未在云端元信息中找到本次测试笔记，尚未初始化。')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'runtime').mkdir()
    (root / 'out').mkdir(mode=0o700)
    source = Path(__file__).resolve().parent
    for name in ('sync_queue.py', 'notehelper_client.py', 'record_plan.py'):
        shutil.copyfile(source / name, root / 'runtime' / name)
    runbook = (source.parent / 'references' / 'scheduled-run.md').read_text(encoding='utf-8')
    rules = source.parent / 'references' / 'recording-rules.md'
    shutil.copyfile(rules, root / 'recording-rules.md')
    (root / 'RUN.md').write_text('# 本机定时同步任务\n\n工作目录：' + str(root) +
        '\n\n解释器：' + sys.executable + '\n\n' + runbook, encoding='utf-8')
    (root / 'CONTINUE.md').write_text(
        '继续同一台电脑、同一用户的笔记同步设置。读取 ' + str(root / 'RUN.md') +
        ' 和 job.json。先检查已登记的定时任务及运行记录，复用现有配置；'
        '若未登记任务，先按已确认规则创建或进入支持定时的入口。不要新建第二套任务。'
        '密钥只按 job.json 中的配置路径读取，不复制到任务提示词或云端。\n', encoding='utf-8')
    job = {'version': 2, 'created_at': now(), 'python': sys.executable,
           'config': str(Path(args.config).resolve()), 'target': args.target,
           'cadence': args.cadence, 'timezone': args.timezone,
           'user_test_confirmed': bool(args.user_tested), 'board': board, 'test_note_id': args.test_note_id,
           'test_doc_url': args.test_doc_url, 'baseline': list(baseline),
           'items': {}, 'schedule': None}
    save(root / 'job.json', job)
    return {'ok': True, 'job': str(root), 'baseline_count': len(baseline),
            'scheduled': False, 'next': ('先按共用流程完成指定资料测试，等用户验收并确认频率后设置定时。'
            if getattr(args, 'manual', False) else '由 Agent 创建宿主定时任务、立即试跑并登记回执；初始化不等于启用。')}


def scan(root, job):
    articles = metadata(job['config'])
    baseline = set(job['baseline'])
    for note_id, article in articles.items():
        if note_id not in baseline and note_id not in job['items']:
            job['items'][note_id] = {'status': 'pending', 'metadata': article}
        elif note_id in job['items'] and job['items'][note_id]['status'] == 'pending':
            job['items'][note_id]['metadata'] = article
            job['items'][note_id]['query'] = ''
    for note_id, item in job['items'].items():
        if item['status'] == 'pending':
            item['available'] = note_id in articles
    save(root / 'job.json', job)
    return {'ok': True, 'pending': sum(i['status'] == 'pending' for i in job['items'].values()),
            'unfinished': sum(i['status'] != 'done' for i in job['items'].values())}


def next_item(root, job, skip_ids=()):
    for note_id, item in job['items'].items():
        if note_id not in skip_ids and item['status'] in ('creating', 'created'):
            return {'ok': True, 'id': note_id, 'status': item['status'],
                    'new_claim': False, 'doc_url': item.get('doc_url'),
                    'source_file': item['source_file'], 'metadata': item['metadata'],
                    'target': job['target'],
                    'next': '复用已有文档读回；没有文档 URL 时先核对不明写入，禁止再次 create。'}
    deferred = []
    for note_id, item in job['items'].items():
        if note_id in skip_ids or item['status'] != 'pending':
            continue
        article = item['metadata']
        if not item.get('available', True):
            item['last_error'] = '本轮云端元信息中不存在，保留待复核，不挡住其它新增。'
            deferred.append(note_id)
            continue
        try:
            response = cloud.search(configured_key(job['config']), item.get('query', ''), 1,
                                    article['result_after'], True, note_id)
        except cloud.SelectionError:
            item['last_error'] = '选定 ID 或位置已变化；下轮重新扫描，不扩大正文范围。'
            deferred.append(note_id)
            continue
        selected = response['articles'][0]
        if not selected['content_available']:
            item['last_error'] = '正文尚未就绪，保留 pending 待下轮重试。'
            deferred.append(note_id)
            continue
        body = selected['content']
        name = hashlib.sha256(note_id.encode()).hexdigest()[:24] + '.md'
        output = root / 'out' / name
        require(not output.exists(), '正文缓存未登记；先核对是否已有飞书写入，再按 recover-source 恢复，不覆盖或重复创建。')
        with output.open('x', encoding='utf-8') as handle:
            handle.write(body)
        if os.name != 'nt':
            output.chmod(0o600)
        item.update(status='creating', source_file='out/' + name,
                    content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                    metadata={k: v for k, v in selected.items() if k != 'content'}, started_at=now())
        item.pop('last_error', None)
        save(root / 'job.json', job)
        return {'ok': True, 'id': note_id, 'status': 'creating', 'new_claim': True,
                'source_file': 'out/' + name,
                'title': selected.get('title'), 'url': selected.get('url'), 'target': job['target'],
                'deferred': deferred,
                'next': '首次领取时只 create 一次；立即 bind 实际文档 URL，然后 fetch 读回核对。'}
    save(root / 'job.json', job)
    unfinished = [k for k, v in job['items'].items() if v['status'] != 'done']
    return {'ok': True, 'status': 'retry_pending' if unfinished else 'idle',
            'deferred': deferred, 'unfinished': unfinished}


def recover_source(root, job, args):
    item = job['items'][args.id]
    if item.get('recovered_source') and item['status'] in ('creating', 'created', 'done'):
        return {'ok': True, 'id': args.id, 'new_claim': False, 'status': item['status'],
                'source_file': item['source_file'], 'doc_url': item.get('doc_url')}
    require(item['status'] == 'pending' and args.no_write_confirmed,
            '仅恢复 pending 孤立缓存；先确认原进程停止、飞书目标和索引没有既有写入。')
    name = hashlib.sha256(args.id.encode()).hexdigest()[:24] + '.md'
    output = root / 'out' / name
    require(output.is_file() and not output.is_symlink() and root.resolve() in output.resolve().parents,
            '缓存必须是本任务 out 内的普通文件，不接受符号链接。')
    selected = cloud.search(configured_key(job['config']), item.get('query', ''), 1,
                            item['metadata']['result_after'], True, args.id)['articles'][0]
    require(selected['id'] == args.id and selected.get('content_available'), '原 ID 正文未就绪，保留原缓存待复核。')
    body = selected['content']
    # Universal-newline read also handles caches originally written on Windows.
    require(output.read_text(encoding='utf-8') == body.replace('\r\n', '\n').replace('\r', '\n'),
            '缓存与当前指定 ID 正文不同，不覆盖、不授予创建许可；先人工核对。')
    item.update(status='creating', source_file='out/' + name, recovered_source=True,
                content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                metadata={k: v for k, v in selected.items() if k != 'content'}, started_at=now())
    item.pop('last_error', None)
    save(root / 'job.json', job)
    return {'ok': True, 'id': args.id, 'status': 'creating', 'new_claim': True,
            'source_file': item['source_file'], 'target': job['target'],
            'next': '仅本次允许创建一次；立即 bind 实际 URL。重试时 new_claim=false，禁止重复创建。'}


def complete(root, job, args):
    root = root.resolve()
    item = job['items'][args.id]
    require(item['status'] == 'created', '需要先绑定实际文档 URL；不能把创建命令成功直接记为完成。')
    receipt_path = (root / args.readback).resolve()
    require(root in receipt_path.parents, '读回回执需保存在本次任务目录内。')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    document = (receipt.get('data') or {}).get('document') or {}
    require(receipt.get('ok') is True and receipt.get('identity') == 'user'
            and document.get('document_id') == doc_token(item['doc_url'])
            and bool(document.get('content')), '回执身份、文档 ID 或正文不符，未记完成。')
    if job.get('board'):
        require(bool(item.get('record_id')) and bool(args.index_readback), '索引尚未绑定/读回，不能只完成正文就记入库。')
        index_path = (root / args.index_readback).resolve()
        require(root in index_path.parents, '索引回执必须在任务目录内。')
        index_receipt = json.loads(index_path.read_text(encoding='utf-8'))
        data = index_receipt.get('data') or {}
        require(index_receipt.get('ok') is True and index_receipt.get('identity') == 'user'
                and item['record_id'] in data.get('record_id_list', []), '索引回执不属于绑定的记录。')
        row = data['data'][data['record_id_list'].index(item['record_id'])]
        cells = dict(zip(data['fields'], row))
        require(item['doc_url'] in str(cells.get('笔记文档', ''))
                and ('NoteHelper ID:' + args.id) in str(cells.get('备注', '')), '索引文档或来源 ID 不匹配。')
        item['index_readback'] = str(index_path.relative_to(root))
    require(args.content_checked and args.images_checked, 'Agent 还需实际核对正文和图片，不只检查退出码。')
    item.update(status='done', completed_at=now(), readback=str(receipt_path.relative_to(root)),
                readback_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest())
    save(root / 'job.json', job)
    return {'ok': True, 'id': args.id, 'status': 'done', 'doc_url': item['doc_url']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('init')
    for flag in ('config', 'target', 'timezone'):
        setup.add_argument('--' + flag, required=True)
    setup.add_argument('--cadence', default='manual')
    setup.add_argument('--test-note-id')
    setup.add_argument('--test-doc-url')
    setup.add_argument('--manual', action='store_true')
    setup.add_argument('--board', help='模板创建后返回的 board.json；新流程必须提供')
    setup.add_argument('--user-tested', action='store_true')
    for name in ('scan', 'status'):
        commands.add_parser(name)
    take = commands.add_parser('next')
    take.add_argument('--skip-id', action='append', default=[],
                      help='仅本轮跳过已处理或待复核项；不删除状态、不记完成，下轮仍会重试')
    bind = commands.add_parser('bind')
    bind.add_argument('--id', required=True)
    bind.add_argument('--doc-url', required=True)
    finish = commands.add_parser('complete')
    finish.add_argument('--id', required=True)
    finish.add_argument('--readback', required=True)
    finish.add_argument('--content-checked', action='store_true')
    finish.add_argument('--images-checked', action='store_true')
    finish.add_argument('--index-readback')
    select = commands.add_parser('select')
    select.add_argument('--id', required=True)
    select.add_argument('--query', required=True)
    select.add_argument('--after', type=int, default=0, help='只读定位时该页的 after 偏移')
    final = commands.add_parser('finalize')
    for flag in ('cadence', 'timezone', 'test-note-id', 'test-doc-url'):
        final.add_argument('--' + flag, required=True)
    final.add_argument('--user-tested', action='store_true')
    recover = commands.add_parser('recover-source')
    recover.add_argument('--id', required=True)
    recover.add_argument('--no-write-confirmed', action='store_true')
    index = commands.add_parser('index-start')
    index.add_argument('--id', required=True)
    index_bind = commands.add_parser('bind-index')
    index_bind.add_argument('--id', required=True)
    index_bind.add_argument('--record-id', required=True)
    owner = commands.add_parser('run-claim')
    owner.add_argument('--owner', required=True)
    release = commands.add_parser('run-release')
    release.add_argument('--owner', required=True)
    parser.add_argument('--owner', help='一轮手动/定时任务共用的运行锁持有者')
    schedule = commands.add_parser('schedule-receipt')
    for flag in ('task-id', 'next-run', 'trial-id'):
        schedule.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    root = args.job.expanduser().resolve()
    if args.command == 'init':
        require(Path(__file__).resolve().parent.parent not in root.parents,
                '同步任务目录不能建在分享包内；请使用固定独立工作目录。')
        cloud.output(initialize(root, args))
        return
    with locked(root):
        job = json.loads((root / 'job.json').read_text(encoding='utf-8'))
        lease = root / '.run.lock'
        if args.command == 'run-claim':
            require(not lease.exists(), '另一轮同步尚未释放运行锁；先核对原任务，不并发创建。')
            save(lease, {'owner': args.owner, 'started_at': now()})
            cloud.output({'ok': True, 'owner': args.owner});return
        if job.get('board') and args.command not in ('status', 'schedule-receipt'):
            require(lease.exists(), '先 run-claim 获取整轮运行锁，再执行手动或定时同步。')
        if args.command != 'status' and lease.exists():
            require(json.loads(lease.read_text()).get('owner') == args.owner, '运行锁不属于本轮同步。')
        if args.command == 'run-release':
            require(lease.exists(), '没有可释放的运行锁。')
            lease.unlink();cloud.output({'ok': True});return
        if args.command == 'select':
            require(args.after >= 0, 'after 不能为负数。')
            if args.id in job['items']:
                result = {'ok': True, 'id': args.id, 'status': job['items'][args.id]['status'], 'duplicate': True}
            else:
                page = cloud.search(configured_key(job['config']), args.query, 50, args.after, False)
                matches = [a for a in page['articles'] if a['id'] == args.id]
                require(len(matches) == 1, '指定资料未在当前搜索页唯一匹配；先只读定位，不扩大正文范围。')
                job['items'][args.id] = {'status': 'pending', 'metadata': matches[0], 'query': args.query}
                save(root / 'job.json', job)
                result = {'ok': True, 'id': args.id, 'status': 'pending'}
        elif args.command == 'finalize':
            require(args.user_tested and args.cadence.strip() and args.cadence.strip() != 'manual'
                    and args.timezone.strip(), '需要用户验收以及明确的频率和时区。')
            item = job['items'].get(args.test_note_id, {})
            doc_token(args.test_doc_url)
            require(item.get('status') == 'done' and item.get('doc_url') == args.test_doc_url,
                    '测试资料必须已完成正文与索引读回，且文档 URL 一致。')
            require(not job.get('schedule') or (job['cadence'] == args.cadence and
                    job['timezone'] == args.timezone), '已有定时任务，不能只改本地频率；先核对宿主任务。')
            job.update(cadence=args.cadence, timezone=args.timezone, user_test_confirmed=True,
                       test_note_id=args.test_note_id, test_doc_url=args.test_doc_url)
            save(root / 'job.json', job)
            result = {'ok': True, 'configuration_ready': True, 'scheduled': False,
                      'next': '仍需创建或复用真实宿主定时任务、试跑并登记回执。'}
        elif args.command == 'recover-source':
            result = recover_source(root, job, args)
        elif args.command == 'index-start':
            item = job['items'][args.id]
            require(item['status'] == 'created' and bool(job.get('board')), '先绑定正文文档和目标模板。')
            new = not item.get('index_started')
            item['index_started'] = True;save(root / 'job.json', job)
            result = {'ok': True, 'new_claim': new, 'record_id': item.get('record_id'),
                      'notice': 'new_claim=false 时先检索来源 ID，不能再次创建索引。'}
        elif args.command == 'bind-index':
            item = job['items'][args.id]
            require(item.get('index_started') and item.get('record_id', args.record_id) == args.record_id
                    and bool(re.fullmatch(r'rec[A-Za-z0-9]+', args.record_id)), '索引状态或绑定不符。')
            item['record_id'] = args.record_id;save(root / 'job.json', job)
            result = {'ok': True, 'record_id': args.record_id}
        elif args.command == 'scan':
            result = scan(root, job)
        elif args.command == 'next':
            result = next_item(root, job, args.skip_id)
        elif args.command == 'bind':
            item = job['items'][args.id]
            doc_token(args.doc_url)
            require(item['status'] in ('creating', 'created') and
                    item.get('doc_url', args.doc_url) == args.doc_url,
                    '已有绑定或状态不符，不能改绑或重新创建。')
            item.update(status='created', doc_url=args.doc_url)
            save(root / 'job.json', job)
            result = {'ok': True, 'id': args.id, 'status': 'created', 'verified': False}
        elif args.command == 'complete':
            result = complete(root, job, args)
        elif args.command == 'schedule-receipt':
            require(job.get('user_test_confirmed') and job['cadence'] != 'manual',
                    '先用 finalize 登记用户验收和频率，再登记真实定时回执。')
            require(not job['schedule'] or job['schedule']['task_id'] == args.task_id,
                    '已登记另一任务，请复用它，不创建重复定时。')
            job['schedule'] = {'task_id': args.task_id, 'next_run': args.next_run,
                               'trial_id': args.trial_id, 'recorded_at': now()}
            save(root / 'job.json', job)
            result = {'ok': True, 'host_receipt_recorded': True,
                      'notice': '本程序不创建或验证宿主任务；Agent 必须核对实际任务详情与试跑结果。'}
        else:
            result = {'ok': True, 'target': job['target'], 'cadence': job['cadence'],
                      'user_test_confirmed': job.get('user_test_confirmed', False),
                      'test_note_id': job.get('test_note_id'), 'test_doc_url': job.get('test_doc_url'),
                      'timezone': job['timezone'], 'schedule': job['schedule'], 'board': job.get('board'),
                      'items': {k: {'status': v['status'], 'doc_url': v.get('doc_url')}
                                for k, v in job['items'].items()}}
    cloud.output(result)


if __name__ == '__main__':
    try:
        main()
    except (cloud.ClientError, OSError, ValueError, KeyError):
        exc = sys.exc_info()[1]
        cloud.output({'ok': False, 'error': str(exc) if isinstance(exc, cloud.ClientError)
                      else '任务状态或本机操作异常；未输出私人正文或密钥，请核对后续接。'})
        sys.exit(1)
