#!/usr/bin/env python3
"""Produce the initial topic field map; never overwrite creative states on later pulls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from notehelper_client import ClientError


INITIAL = {'选题状态': ['待研究'], 'route': ['待定']}
SOURCE_FIELDS = ('发布时间', '点赞', '收藏', '评论', '转发', '平台')


def topic_fields(article, doc_url, facts=None, existing=None):
    facts = facts or {}
    if existing is not None:
        # Repeated pulls never write creative fields, notes, rewrites, or versions.
        return {}
    if not article.get('id') or not article.get('title'):
        raise ClientError('缺少稳定来源 ID 或标题，不能创建索引。')
    fields = dict(INITIAL, 标题=article['title'], 作者=article.get('author'),
                  原文链接=article.get('url'), 笔记文档=doc_url,
                  抓取时间=datetime.now(timezone.utc).isoformat(),
                  备注='NoteHelper ID:' + article['id'])
    for key in SOURCE_FIELDS:
        if facts.get(key) is not None:
            fields[key] = facts[key]
    return fields


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--article', type=Path, required=True)
    p.add_argument('--doc-url', required=True)
    p.add_argument('--facts', type=Path)
    p.add_argument('--existing', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    read = lambda path: json.loads(path.read_text(encoding='utf-8')) if path else None
    result = topic_fields(read(a.article), a.doc_url, read(a.facts), read(a.existing))
    with a.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'ok': True, 'field_count': len(result), 'output': str(a.output)}, ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except (ClientError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ClientError) else '索引文件操作失败。'}, ensure_ascii=False))
        sys.exit(1)
