"""Cloud observers: dispatch only live.yml; read IC signals, never write repo data."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
from pathlib import Path
import re
import subprocess

from backend_io import write_json_atomic
from market_calendar import TZ, is_trading_day

ROOT = Path(__file__).resolve().parent
REPO = 'kakarottoisme/national-team-octagon'
REPO_URL = 'https://github.com/' + REPO
LIVE_SOURCE = REPO_URL + '/blob/main/data/live.json'
SIGNAL_SOURCE = REPO_URL + '/blob/main/data/ic_strategy.json'
HOLDINGS_SOURCE = REPO_URL + '/blob/main/data/holdings.json'
ACTIVE_STATUSES = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
SIGNAL_FIELDS = ('leg_a', 'leg_b', 'b_open', 'leg_c', 'leg_d', 'total')


def session(now):
    minute = now.hour * 60 + now.minute
    if 560 <= minute <= 695:
        return 'am'
    if 775 <= minute <= 905:
        return 'pm'
    return None


def allowed(task, now):
    minute = now.hour * 60 + now.minute
    return bool(session(now)) if task == 'live-recover' else 1230 <= minute <= 1290


def load(name):
    return json.loads((ROOT / 'data' / name).read_text(encoding='utf-8'))


def gh(*args):
    # Do not return stderr, URLs containing credentials, or gh auth internals.
    try:
        return subprocess.check_output(['gh', *args], stderr=subprocess.PIPE, timeout=45).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('GitHub 请求未确认成功，请检查授权或服务状态') from None


def active_live_runs():
    endpoint = f'repos/{REPO}/actions/workflows/live.yml'
    meta = json.loads(gh('api', endpoint))
    if meta.get('name') != 'Refresh live.json' or meta.get('path') != '.github/workflows/live.yml' or meta.get('state') != 'active':
        raise ValueError('既有 Refresh live.json 工作流不可用，未启动其他工作流')
    yaml = (ROOT / '.github/workflows/live.yml').read_text()
    if not re.search(r'group:\s*refresh-live-json\s*$', yaml, re.M) or not re.search(r'cancel-in-progress:\s*false\s*$', yaml, re.M):
        raise ValueError('刷新工作流并发约束不匹配，未手动启动')
    for status in ACTIVE_STATUSES:
        raw = gh('api', endpoint + '/runs', '--method', 'GET', '-f', 'status=' + status,
                 '-f', 'per_page=100', '--paginate', '--jq', '.workflow_runs')
        # Each paginated response is an array; jq defaults to pretty printing.
        decoder = json.JSONDecoder()
        rest = raw.strip()
        while rest:
            rows, end = decoder.raw_decode(rest)
            if rows:
                return True  # Includes other branches sharing the concurrency group.
            rest = rest[end:].lstrip()
    return False


def dispatch_live():
    gh('workflow', 'run', 'live.yml', '--repo', REPO, '--ref', 'main')


def live_stamp(raw, now):
    stamp = dt.datetime.fromisoformat(raw['updated'])
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TZ)
    stamp = stamp.astimezone(TZ)
    if stamp > now + dt.timedelta(seconds=30):
        raise ValueError('行情更新时间在未来，不能判为恢复')
    return stamp


def warning(state, now, message, persist, sources):
    today = now.date().isoformat()
    if state.get('warned_day') == today:
        return {'notify': False, 'reason': '当日已提醒'}
    state['warned_day'] = today
    persist(state)
    return {'notify': True, 'message': message, 'sources': sources}


def live_recover(now, state, persist, busy=active_live_runs, dispatch=dispatch_live):
    half = session(now)
    if half is None or not is_trading_day(now.date()):
        return {'notify': False, 'reason': '不在交易检查窗口'}
    today = now.date().isoformat()
    key = today + '-' + half
    attempt = state.get('attempts', {}).get(key)
    if attempt:
        if attempt.get('checked'):
            return {'notify': False, 'reason': '本半日已检查恢复结果'}
        due = dt.datetime.fromisoformat(attempt['followup_at'])
        if now < due:
            return {'notify': False, 'reason': '等待恢复', 'followup_at': due.isoformat()}
        attempt['checked'] = True
        raw = load('live.json')
        stamp = live_stamp(raw, now)
        started = dt.datetime.fromisoformat(attempt['started_at'])
        if raw.get('date') == now.strftime('%Y%m%d') and stamp > started and (now - stamp).total_seconds() <= 900:
            persist(state)
            return {'notify': False, 'reason': '恢复成功'}
        return warning(state, now, '盘中刷新自恢复未成功：补启动后15分钟仍未看到当日行情更新。', persist, [LIVE_SOURCE])
    if state.get('warned_day') == today:
        return {'notify': False, 'reason': '当日已提醒'}
    # Even stale data cannot authorize a duplicate dispatch while queued/running.
    if busy():
        return {'notify': False, 'reason': '已有运行或排队实例，未重复启动'}
    raw = load('live.json')
    live_stamp(raw, now)  # Validate real timestamp; never use file access time.
    if busy():  # Recheck immediately before reserving the dispatch.
        return {'notify': False, 'reason': '实例刚开始运行或排队，未重复启动'}
    due = now + dt.timedelta(minutes=15)
    # Reserve before side effect: a timeout must not cause a second dispatch.
    state['attempts'] = {k: v for k, v in state.get('attempts', {}).items() if k.startswith(today)}
    state['attempts'][key] = {'started_at': now.isoformat(), 'followup_at': due.isoformat(), 'checked': False, 'dispatch': 'reserved'}
    persist(state)
    try:
        dispatch()
        state['attempts'][key]['dispatch'] = 'accepted'
    except ValueError:
        state['attempts'][key]['dispatch'] = 'unconfirmed'
    persist(state)
    return {'notify': False, 'reason': '已尝试补启动，等待复核', 'followup_at': due.isoformat()}


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('IC 信号或持仓字段无效：' + name)
    return value


def signal_snapshot(raw, now):
    latest = dt.date.fromisoformat(raw['latest'])
    if latest > now.date() or not is_trading_day(latest):
        raise ValueError('IC 信号交易日无效')
    if latest != now.date():
        return None  # Old data is not an error and cannot establish today's baseline.
    days = raw['days']
    if not days or days[-1].get('date') != raw['latest']:
        raise ValueError('IC latest 与最后一条信号日期不一致')
    row = days[-1]
    values = {k: row[k] for k in SIGNAL_FIELDS}
    integer(values['leg_a'], 'leg_a', 0, 10)
    integer(values['leg_b'], 'leg_b', 0, 2)
    integer(values['leg_c'], 'leg_c', -10, 10)
    integer(values['leg_d'], 'leg_d', 0, 4)
    integer(values['total'], 'total', 0, 10)
    if values['leg_b'] not in (0, 2) or type(values['b_open']) is not bool or values['b_open'] != (values['leg_b'] == 2):
        raise ValueError('IC 趋势开关与B腿不一致')
    expected = min(10, min(10, max(0, values['leg_a'] + values['leg_b'] + values['leg_c'])) + values['leg_d'])
    if expected != values['total']:
        raise ValueError('IC 总手数与信号不一致')
    meta = raw['d_leg']
    if meta.get('margin_rate') != .12 or meta.get('total_cap') != 10 or meta.get('d_max') != 4:
        raise ValueError('IC 保证金或手数上限与约定不一致')
    text = row['sum_text']
    compact = re.sub(r'\s+', '', text).replace('−', '-')
    match = re.fullmatch(r'A(\d+)\+B(\d+)([+-])C(\d+)\+D(\d+)=(\d+)(?:（底仓未限制前-?\d+）)?', compact)
    if not match:
        raise ValueError('IC 公式文案格式无效')
    aa, bb, sign, cc, dd, total = match.groups()
    formula = (int(aa), int(bb), int(cc) * (-1 if sign == '-' else 1), int(dd), int(total))
    if formula != tuple(values[k] for k in ('leg_a', 'leg_b', 'leg_c', 'leg_d', 'total')):
        raise ValueError('IC 公式文案与信号数字不一致')
    return {'date': raw['latest'], 'values': values, 'sum_text': text}


def position_text(holdings, total):
    positions = holdings['positions']
    if len(positions) != 1:
        raise ValueError('持仓必须是单一IC合约，未推测实际手数')
    pos = positions[0]
    if not re.fullmatch(r'IC\d{4}', str(pos.get('contract', ''))) or pos.get('direction') not in ('多', '空'):
        raise ValueError('持仓合约或方向无效')
    lots = integer(pos['lots'], 'lots', 0, 100)
    if pos['direction'] == '空' and lots:
        return f"当前{pos['contract']}空{lots}手；按多头策略需先平空{lots}手，再开多{total}手（净头寸增加{lots + total}手）"
    difference = total - lots
    action = f'应加{difference}手' if difference > 0 else f'应减{-difference}手' if difference < 0 else '手数无需调整'
    return f"当前{pos['contract']}多{lots}手，{action}"


def ic_signal(now, state, persist):
    if not allowed('ic-signal', now) or not is_trading_day(now.date()):
        return {'notify': False, 'reason': '不在交易检查窗口'}
    today = now.date().isoformat()
    if state.get('notified_day') == today or state.get('warned_day') == today:
        return {'notify': False, 'reason': '当日已提醒'}
    current = signal_snapshot(load('ic_strategy.json'), now)
    if current is None:
        return {'notify': False, 'reason': '文件未推进至当日信号'}
    previous = state.get('last_notified')
    if previous is None:
        state['last_notified'] = current
        state['last_seen_day'] = current['date']
        persist(state)
        return {'notify': False, 'reason': '首次建立基线'}
    if current['date'] <= state.get('last_seen_day', previous['date']):
        return {'notify': False, 'reason': '信号日未推进'}
    if state.get('notified_day') == now.date().isoformat():
        return {'notify': False, 'reason': '当日已提醒'}
    changes = [k for k in SIGNAL_FIELDS if previous['values'][k] != current['values'][k]]
    if not changes:
        state['last_seen_day'] = current['date']
        persist(state)
        return {'notify': False, 'reason': '信号无变化'}
    position = position_text(load('holdings.json'), current['values']['total'])
    labels = {'leg_a': '网格A', 'leg_b': '趋势B', 'b_open': '趋势开关', 'leg_c': '贴水C', 'leg_d': '盈利加仓D', 'total': '总手数'}
    def value(key, snapshot):
        v = snapshot['values'][key]
        return ('打开' if v else '关闭') if key == 'b_open' else str(v)
    reason = '、'.join(f'{labels[k]}由{value(k, previous)}变为{value(k, current)}' for k in changes)
    state['last_notified'] = current
    state['notified_day'] = now.date().isoformat()
    persist(state)
    return {'notify': True, 'message': f"IC信号变化｜{current['date']}｜{current['sum_text']}。{position}。变化：{reason}。",
            'link': 'https://kakarottoisme.github.io/national-team-octagon/futures-preview.html',
            'sources': [SIGNAL_SOURCE, HOLDINGS_SOURCE]}


def execute(task, now, state, persist):
    if not allowed(task, now):
        return {'notify': False, 'reason': '不在授权窗口'}
    try:
        if not is_trading_day(now.date()):
            return {'notify': False, 'reason': '非交易日'}
        return live_recover(now, state, persist) if task == 'live-recover' else ic_signal(now, state, persist)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '来源或字段校验失败（' + type(exc).__name__ + '）'
        label = '盘中自恢复' if task == 'live-recover' else 'IC信号检查'
        sources = [LIVE_SOURCE, REPO_URL + '/blob/main/.github/workflows/live.yml'] if task == 'live-recover' else [SIGNAL_SOURCE, HOLDINGS_SOURCE]
        return warning(state, now, label + '失败：' + message, persist, sources)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=('live-recover', 'ic-signal'))
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    now = dt.datetime.now(TZ)
    if not allowed(args.task, now):
        print(json.dumps({'notify': False, 'reason': '不在授权窗口'}, ensure_ascii=False))
        return
    directory = Path(args.state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (args.task + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / (args.task + '.json')
        state = json.loads(path.read_text()) if path.exists() else {}
        def persist(value):
            write_json_atomic(path, value, ensure_ascii=False, indent=2)
        result = execute(args.task, now, state, persist)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
