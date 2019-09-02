#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import time
import json
import random
import logging
import collections
import configparser

import requests

logging.basicConfig(stream=sys.stderr, format='%(asctime)s [%(name)s:%(levelname)s] %(message)s', level=logging.DEBUG if sys.argv[-1] == '-v' else logging.INFO)

HSession = requests.Session()

re_mdescape = re.compile(r'([\[\*_])')
mdescape = lambda s: re_mdescape.sub(r'\\\1', s)

class BotAPIFailed(Exception):
    def __init__(self, ret):
        self.ret = ret
        self.description = ret['description']
        self.error_code = ret['error_code']
        self.parameters = ret.get('parameters')

    def __repr__(self):
        return 'BotAPIFailed(%r)' % self.ret

class TelegramBotClient:
    def __init__(self, apitoken, username=None, config=None):
        self.token = apitoken
        if username:
            self.username = username
        else:
            self.username = self.bot_api('getMe')['username']
        self.config = config
        self.offset = None
        self.run = True

    def bot_api(self, method, **params):
        for att in range(3):
            try:
                req = HSession.post(('https://api.telegram.org/bot%s/' %
                                    self.token) + method, data=params, timeout=45)
                retjson = req.content
                ret = json.loads(retjson.decode('utf-8'))
                break
            except Exception as ex:
                if att < 1:
                    time.sleep((att + 1) * 2)
                else:
                    raise ex
        if not ret['ok']:
            raise BotAPIFailed(ret)
        return ret['result']

    def parse_cmd(self, text: str):
        t = text.strip().replace('\xa0', ' ').split(' ', 1)
        if not t:
            return None, None
        cmd = t[0].rsplit('@', 1)
        if len(cmd[0]) < 2 or cmd[0][0] != '/':
            return None, None
        if len(cmd) > 1 and cmd[-1] != self.username:
            return None, None
        expr = t[1] if len(t) > 1 else ''
        return cmd[0][1:], expr

    def serve(self, **kwargs):
        '''
        **kwargs is a map for callbacks. For example: {'message': process_msg}
        '''
        while self.run:
            try:
                updates = self.bot_api('getUpdates', offset=self.offset, timeout=30)
            except BotAPIFailed as ex:
                if ex.parameters and 'retry_after' in ex.parameters:
                    time.sleep(ex.parameters['retry_after'])
            except Exception:
                logging.exception('Get updates failed.')
                continue
            if not updates:
                continue
            self.offset = updates[-1]["update_id"] + 1
            for upd in updates:
                for k, v in upd.items():
                    if k == 'update_id':
                        continue
                    elif kwargs.get(k):
                        kwargs[k](self, v)
            time.sleep(.2)

    def __getattr__(self, name):
        return lambda **kwargs: self.bot_api(name, **kwargs)

apiheader = {'X-Requested-With': 'XMLHttpRequest'}

def message_handler(cli, msg):
    msgtext = msg.get('text', '')
    cmd, expr = cli.parse_cmd(msgtext)
    cmds = {
        'pkgver': cmd_pkgver,
        'search': cmd_search,
        'getupdreq': cmd_getupdreq,
        'start': lambda *args: None
    }
    if not cmd:
        return
    elif cmd in cmds:
        try:
            ret = cmds[cmd](cli, msg, expr)
            logging.info('Command: ' + msgtext)
        except Exception:
            logging.exception('Failed command: ' + msgtext)
            ret = "Failed to fetch data. Please try again later."
        if not ret:
            return
        try:
            cli.sendMessage(chat_id=msg['chat']['id'], text=ret,
                            parse_mode='Markdown', disable_web_page_preview=True)
        except Exception:
            logging.exception('Failed to send: ' + ret)

def cmd_pkgver(cli, msg, expr):
    package = expr.strip()
    if not package:
        return
    url = cli.config['API']['endpoint'] + 'packages/' + package
    url2 = cli.config['API']['urlhead'] + 'packages/' + package
    req = HSession.get(url, timeout=10, headers=apiheader)
    d = req.json()
    if req.status_code == 404:
        return mdescape(d['error'])
    req.raise_for_status()
    pkg = d['pkg']
    text = ['Package: [%s](%s)' % (package, url2),
            '*source*: ' + (pkg.get('full_version') or 'missing')]
    repos = collections.OrderedDict()
    for repo, dpkgs in pkg['dpkg_matrix']:
        for dpkg in dpkgs:
            if not dpkg or dpkg['repo'] in repos:
                continue
            else:
                repos[dpkg['repo']] = dpkg['version']
    text.extend('*%s*: %s' % kv for kv in repos.items())
    if pkg.get('upstream'):
        text.append('*upstream*: [%s](%s)' % (
            pkg['upstream']['version'], pkg['upstream']['url']))
    return '\n'.join(text)

def cmd_search(cli, msg, expr):
    package = expr.strip()
    if not package:
        return
    url = cli.config['API']['endpoint'] + ('search/?q=%s&noredir=1' % package)
    url2 = cli.config['API']['urlhead'] + ('search/?q=%s&noredir=1' % package)
    req = HSession.get(url, timeout=10, headers=apiheader)
    d = req.json()
    if req.status_code == 404:
        return mdescape(d['error'])
    req.raise_for_status()
    if len(d['packages'] ) == 0: return 'Sorry, no result'
    text = ['Search: [%s](%s)' % (package, url2)]
    for pkg, _ in zip(d['packages'], range(5)):
        text.append('*%s* %s' % (pkg['name'], pkg['full_version']))
    return '\n'.join(text)

def cmd_getupdreq(cli, msg, expr):
    url = cli.config['API']['endpoint'] + 'srcupd/aosc-os-abbs?page=all'
    req = HSession.get(url, timeout=10, headers=apiheader)
    d = req.json()
    if req.status_code == 404:
        return mdescape(d['error'])
    req.raise_for_status()
    text = []
    for pkg in random.sample(d['packages'], 5):
        text.append('*%s* [%s](%s) → [%s](%s)' % (
            pkg['name'], pkg['version'],
            cli.config['API']['urlhead'] + 'packages/' + pkg['name'],
            pkg['upstream_version'], pkg['upstream_url']))
    return '\n'.join(text)

def load_config(filename):
    cp = configparser.ConfigParser()
    cp.read(filename)
    return cp

def main():
    config = load_config('config.ini')
    botcli = TelegramBotClient(
        config['Bot']['apitoken'], config['Bot'].get('username'), config)
    logging.info('Satellite launched.')
    botcli.serve(message=message_handler)

if __name__ == '__main__':
    main()
