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
    require(args.user_tested, '需要用户确认亲自转发测试成功后再初始化定时同步。')
    require(not root.exists(), '任务目录已存在；请读取原 job.json 和 status 续接，不重新初始化。')
    require(Path(args.config).is_file(), '请使用已经验证的本机密钥配置文件。')
    doc_token(args.test_doc_url)
    baseline = metadata(args.config)
    require(args.test_note_id in baseline, '未在云端元信息中找到本次测试笔记，尚未初始化。')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'runtime').mkdir()
    (root / 'out').mkdir(mode=0o700)
    source = Path(__file__).resolve().parent
    for name in ('sync_queue.py', 'notehelper_client.py'):
        shutil.copyfile(source / name, root / 'runtime' / name)
    runbook = (source.parent / 'references' / 'scheduled-run.md').read_text(encoding='utf-8')
    (root / 'RUN.md').write_text('# 本机定时同步任务\n\n工作目录：' + str(root) +
        '\n\n解释器：' + sys.executable + '\n\n' + runbook, encoding='utf-8')
    (root / 'CONTINUE.md').write_text(
        '继续同一台电脑、同一用户的笔记同步设置。读取 ' + str(root / 'RUN.md') +
        ' 和 job.json。先检查已登记的定时任务及运行记录，复用现有配置；'
        '若未登记任务，先按已确认规则创建或进入支持定时的入口。不要新建第二套任务。'
        '密钥只按 job.json 中的配置路径读取，不复制到任务提示词或云端。\n', encoding='utf-8')
    job = {'version': 1, 'created_at': now(), 'python': sys.executable,
           'config': str(Path(args.config).resolve()), 'target': args.target,
           'cadence': args.cadence, 'timezone': args.timezone,
           'user_test_confirmed': True, 'test_note_id': args.test_note_id,
           'test_doc_url': args.test_doc_url, 'baseline': list(baseline),
           'items': {}, 'schedule': None}
    save(root / 'job.json', job)
    return {'ok': True, 'job': str(root), 'baseline_count': len(baseline),
            'scheduled': False, 'next': '由 Agent 创建宿主定时任务、立即试跑并登记回执；初始化不等于启用。'}


def scan(root, job):
    articles = metadata(job['config'])
    baseline = set(job['baseline'])
    for note_id, article in articles.items():
        if note_id not in baseline and note_id not in job['items']:
            job['items'][note_id] = {'status': 'pending', 'metadata': article}
        elif note_id in job['items'] and job['items'][note_id]['status'] == 'pending':
            job['items'][note_id]['metadata'] = article
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
            response = cloud.search(configured_key(job['config']), '', 1,
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
        require(not output.exists(), '正文文件已存在但未登记完成，请核对中断状态，不覆盖或重复创建。')
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
    for flag in ('config', 'target', 'cadence', 'timezone', 'test-note-id', 'test-doc-url'):
        setup.add_argument('--' + flag, required=True)
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
        if args.command == 'scan':
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
            require(not job['schedule'] or job['schedule']['task_id'] == args.task_id,
                    '已登记另一任务，请复用它，不创建重复定时。')
            job['schedule'] = {'task_id': args.task_id, 'next_run': args.next_run,
                               'trial_id': args.trial_id, 'recorded_at': now()}
            save(root / 'job.json', job)
            result = {'ok': True, 'host_receipt_recorded': True,
                      'notice': '本程序不创建或验证宿主任务；Agent 必须核对实际任务详情与试跑结果。'}
        else:
            result = {'ok': True, 'target': job['target'], 'cadence': job['cadence'],
                      'timezone': job['timezone'], 'schedule': job['schedule'],
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
