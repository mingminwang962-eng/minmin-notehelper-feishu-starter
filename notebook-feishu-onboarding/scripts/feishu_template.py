#!/usr/bin/env python3
"""Create an account-independent Base from the bundled original-layout template."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from sync_queue import save
from notehelper_client import ClientError


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Provisioner:
    def __init__(self, root):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'template-state.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'operations': {}}

    def call(self, key, args, write=False):
        old = self.state['operations'].get(key)
        if old:
            if old.get('done'):
                return old['data']
            raise ClientError('上次写入结果未明：' + key + '。先核对飞书和回执，不自动重复创建。')
        if write:
            self.state['operations'][key] = {'done': False, 'argv': args}
            save(self.path, self.state)
        proc = subprocess.run(['lark-cli'] + args + ['--as', 'user'], capture_output=True, text=True)
        receipt = self.root / (key + '.json')
        receipt.write_text(proc.stdout or proc.stderr, encoding='utf-8')
        try:
            result = json.loads(proc.stdout or proc.stderr)
        except ValueError:
            raise ClientError('飞书 CLI 未返回可核验的 JSON；回执已保存。')
        if proc.returncode or result.get('ok') is not True or result.get('identity') != 'user':
            raise ClientError('飞书操作未通过：' + key + '；按本地回执处理，未输出私人内容。')
        self.state['operations'][key] = {'done': True, 'data': result['data']}
        save(self.path, self.state)
        print(key + ' OK', file=sys.stderr)
        return result['data']

    def provision(self, template, name, timezone):
        tables = template['tables']
        plain = lambda n: [f for f in tables[n]['fields'] if f['type'] != 'link']
        first = next(iter(tables))
        created = self.call('base', ['base', '+base-create', '--name', name, '--time-zone', timezone,
                                   '--table-name', first, '--fields', encode(plain(first))], True)
        base_info = created.get('base', created)
        base = base_info.get('base_token') or base_info.get('app_token')
        if not base:
            raise ClientError('创建回执缺少 Base ID，保留回执核对，不猜测。')
        ids = {}
        for n in tables:
            if n != first:
                self.call('table-' + n, ['base', '+table-create', '--base-token', base,
                          '--name', n, '--fields', encode(plain(n))], True)
        found = self.call('tables', ['base', '+table-list', '--base-token', base])
        for t in found.get('tables', found.get('items', [])):
            ids[t['name']] = t.get('id') or t.get('table_id')
        if any(n not in ids for n in tables):
            raise ClientError('四张业务表未全部发现，停止创建关联。')
        made = set()
        for n, table in tables.items():
            for f in table['fields']:
                if f['type'] != 'link' or (n, f['name']) in made:
                    continue
                target = f['link_table']
                reverse = next(r for r in tables[target]['fields']
                               if r['type'] == 'link' and r['link_table'] == n)
                definition = dict(f, link_table=ids[target], bidirectional_link_field_name=reverse['name'])
                self.call('link-' + n + '-' + f['name'], ['base', '+field-create', '--base-token', base,
                          '--table-id', ids[n], '--json', encode(definition)], True)
                made.update([(n, f['name']), (target, reverse['name'])])
        for n, table in tables.items():
            for i, view in enumerate(table['views']):
                key = 'view-' + n + '-' + str(i)
                if i == 0 and view['type'] == 'grid':
                    # The platform creates the default grid. Configure it instead of adding a second one.
                    existing = self.call(key + '-list', ['base', '+view-list', '--base-token', base,
                                         '--table-id', ids[n]])['views']
                    vid = existing[0]['id']
                else:
                    result = self.call(key, ['base', '+view-create', '--base-token', base, '--table-id', ids[n],
                                       '--json', encode({'name': view['name'], 'type': view['type']})], True)
                    vid = view['name']
                for kind in ['visible', 'filter', 'group', 'card']:
                    if kind not in view:
                        continue
                    value = view[kind]
                    if kind == 'group': value = {'group_config': value.get('group', [])}
                    if kind == 'card': value = value.get('card', value)
                    command = 'visible-fields' if kind == 'visible' else kind
                    self.call(key + '-' + kind, ['base', '+view-set-' + command, '--base-token', base,
                              '--table-id', ids[n], '--view-id', vid, '--json', encode(value)], True)
        dashboards = {}
        for i, dashboard in enumerate(template['dashboards']):
            key = 'dashboard-' + str(i)
            result = self.call(key, ['base', '+dashboard-create', '--base-token', base,
                               '--name', dashboard['name']], True)
            dashboard_info = result.get('dashboard', result)
            did = dashboard_info.get('dashboard_id') or dashboard_info.get('id')
            if not did: raise ClientError('看板回执缺少 ID，请核对后续接。')
            dashboards[dashboard['name']] = did
            for j, block in enumerate(dashboard['blocks']):
                self.call(key + '-' + str(j), ['base', '+dashboard-block-create', '--base-token', base,
                          '--dashboard-id', did, '--name', block['name'], '--type', block['type'],
                          '--data-config', encode(block['data_config']), '--position', encode(block['position'])], True)
        mapping = {'version': 1, 'base_token': base, 'tables': ids, 'dashboards': dashboards,
                   'url': base_info.get('url') or 'https://my.feishu.cn/base/' + base}
        save(self.root / 'board.json', mapping)
        return mapping


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workdir', type=Path, required=True)
    p.add_argument('--name', default='内容创作模板')
    p.add_argument('--timezone', required=True)
    p.add_argument('--template', type=Path, default=Path(__file__).resolve().parent.parent / 'assets/feishu-template.json')
    a = p.parse_args()
    if Path(__file__).resolve().parent.parent in a.workdir.resolve().parents:
        raise ClientError('模板运行回执不能保存到分享包内。')
    result = Provisioner(a.workdir).provision(json.loads(a.template.read_text(encoding='utf-8')), a.name, a.timezone)
    print(encode({'ok': True, 'board': result}))


if __name__ == '__main__':
    try: main()
    except (ClientError, OSError, ValueError, KeyError, StopIteration) as exc:
        print(encode({'ok': False, 'error': str(exc) if isinstance(exc, ClientError) else '模板操作异常；保留本地回执核对后续接。'}))
        sys.exit(1)
