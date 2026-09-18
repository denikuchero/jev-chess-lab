#!/usr/bin/env python3
"""Jev (white) vs a small depth-2 material-search opponent (black)."""
import argparse
import datetime
import html
import json
from pathlib import Path
import random
import time

import chess
import chess.pgn
import chess.svg
import chess.engine

import jev
from chess_safety import filter_moves
from chess_coach import engine_path, engine_filter

VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
DEFAULT_POLICY = 'raw'
PURE_PROMPT_VERSION = 'pure-v2-history'


def evaluate(board):
    if board.is_checkmate():
        return -100000 if board.turn == chess.WHITE else 100000
    if board.is_game_over(claim_draw=True):
        return 0
    return sum(value * (len(board.pieces(piece, chess.WHITE)) - len(board.pieces(piece, chess.BLACK)))
               for piece, value in VALUES.items())


def search(board, depth):
    if depth == 0 or board.is_game_over(claim_draw=True):
        return evaluate(board)
    scores = []
    for move in list(board.legal_moves):
        board.push(move)
        scores.append(search(board, depth - 1))
        board.pop()
    return max(scores) if board.turn == chess.WHITE else min(scores)


def opponent_move(board, rng):
    moves = list(board.legal_moves)
    rng.shuffle(moves)
    scored = []
    for move in moves:
        board.push(move)
        score = search(board, 1)
        board.pop()
        scored.append((score, move))
    best = (max if board.turn == chess.WHITE else min)(s for s, _ in scored)
    return next(move for score, move in scored if score == best)


def make_request(board, model, safety=None):
    # Only legal move names, no engine scores or suggested best moves.
    criteria = {move.uci(): board.san(move) for move in sorted(board.legal_moves, key=lambda m: m.uci())}
    if safety and safety.get('kind') == 'engine':
        rows = {r['uci']: r for r in safety['candidates']}
        criteria = {uci: f'{criteria[uci]}; engine evaluation for White: '
                    + (f'mate in {rows[uci]["mate_in"]}' if rows[uci]['mate_in'] is not None else f'{rows[uci]["score_cp"]/100:+.2f} pawns')
                    + f'; repeated position: {rows[uci]["repeats_position"]}' for uci in safety['offered']}
    elif safety:
        criteria = {uci: f'{criteria[uci]}; estimated immediate material change: {safety["analysis"][uci]["material_delta_cp"]/100:+.2f} pawns'
                    for uci in safety['offered']}
    rows = [f'{rank+1} ' + ' '.join(board.piece_at(chess.square(file, rank)).symbol()
                                  if board.piece_at(chess.square(file, rank)) else '.' for file in range(8))
            for rank in range(7, -1, -1)]
    state = {"fen": board.fen(), "board": '\n'.join(rows) + '\n  a b c d e f g h',
             "legend": "Uppercase = White, lowercase = Black. P pawn, N knight, B bishop, R rook, Q queen, K king; . empty.",
             "you_play": "White", "in_check": board.is_check(),
             "history_uci": [m.uci() for m in board.move_stack]}
    history_board = board.root()
    history_san = []
    for move in board.move_stack:
        history_san.append(history_board.san(move))
        history_board.push(move)
    state['history_san'] = history_san
    if safety and safety.get('kind') == 'engine':
        state['engine_coach'] = {'reason': safety['reason'], 'candidates': safety['candidates'],
                                 'limits': 'Stockfish shortlist and evaluations, not a guarantee. Choose only from offered criteria. Preserve winning advantage, avoid repetitions when ahead. Engine scores are not win probabilities.'}
    elif safety:
        state['tactical_filter'] = {'reason': safety['reason'], 'excluded_moves': safety['excluded_count'],
                                    'limits': 'Only immediate opponent captures, legal same-square recapture sequences, and opponent mate in one checked. Not a full search; forks and later tactics may be missed.'}
    body = {"model": model, "state": state, "questions": {"move": {
        "type": "choice", "instructions": "Play standard chess as White. Choose the strongest legal move to win. Consider opponent replies, hanging pieces, threats, king safety and checkmate. Keys are UCI from-square/to-square (optional promotion), descriptions are SAN. Choose exactly one offered move.",
        "criteria": criteria}}}
    if safety:
        body['questions']['move']['instructions'] += (' Prioritize winning material safely, protect ALL your pieces, develop pieces, castle and coordinate attacks. A check is not automatically a good move. Avoid repeated queen/rook checks without benefit. Never assume a defended piece is safe: trading a queen for a pawn loses material. Consider opponent captures and forks after every candidate. Finish with mate when available. The local filter is limited and not proof of safety.')
    else:
        body['questions']['move']['instructions'] += (' You are the sole decision maker: no move has been ranked or filtered for strength. Independently examine opponent checks, captures and threats after your proposed move, including attacks on other pieces. A check or capture is not automatically good. A defended queen can still lose material against a cheaper attacker; assess the full exchange. Develop pieces and coordinate them, protect your king, and convert advantages into checkmate. Use both complete move histories to recognize unproductive repeated maneuvers; avoid repeating positions when you judge that you can win, but consider a draw when losing. These are general instructions, not tactical hints for this position.')
    return body


def save(folder, game, board, records, requests, reason, model, seed):
    outcome = board.outcome(claim_draw=True)
    result = outcome.result() if outcome else '*'
    game.headers['Result'] = result
    game.headers['Termination'] = reason
    (folder / 'game.pgn').write_text(str(game) + '\n', encoding='utf-8')
    usages = [r.get('response', {}).get('usage', {}) for r in requests]
    summary = {"result": result, "reason": reason, "plies": len(records), "api_requests": len(requests),
               "input_tokens": sum(u.get('input_tokens', 0) for u in usages),
               "output_tokens": sum(u.get('output_tokens', 0) for u in usages),
               "reported_cost_usd": sum(u['cost'] for u in usages if isinstance(u.get('cost'), (int, float))),
               "requests_with_cost": sum(isinstance(u.get('cost'), (int, float)) for u in usages)}
    assisted = game.headers.get('JevPolicy') in ('guarded', 'engine')
    data = dict(model=model, seed=seed, policy=game.headers.get('JevPolicy', 'raw'), opponent=game.headers['Black'], settings=dict(game.headers),
                summary=summary, moves=records, requests=requests)
    (folder / 'game.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    positions = [chess.STARTING_FEN] + [r['fen_after'] for r in records]
    svgs = [chess.svg.board(chess.Board(fen), lastmove=chess.Move.from_uci(records[i-1]['uci']) if i else None,
                            size=500) for i, fen in enumerate(positions)]
    labels = ['Начальная позиция'] + [f'{(r["ply"]+1)//2}{"." if r["ply"]%2 else "..."} {r["actor"]}: {r["san"]} ({r["uci"]}) — {r["elapsed_s"]:.2f} с'
        + (f' | Фильтр исключил {r["safety"]["excluded_count"]} из {r["safety"]["legal_count"]} ходов' if r.get('safety') else '') for r in records]
    packed = json.dumps(dict(boards=svgs, labels=labels), ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Jev играет в шахматы</title><style>body{max-width:850px;margin:30px auto;padding:0 20px;font:17px system-ui;background:#f3f5f9;color:#17243b}#board{max-width:500px}button{padding:10px;margin:4px}input{width:100%}pre{white-space:pre-wrap}svg{width:100%;height:auto}</style>
<h1>Jev — белые; простой бот — чёрные</h1><p>Jev получает позицию и все легальные ходы. Подсказок от движка нет. Вероятности выбора не являются шансами победы.</p>
<div id="board"></div><p id="label"></p><button id="prev">← Назад</button><button id="play">▶ Воспроизвести</button><button id="next">Вперёд →</button><input id="slider" type="range" min="0" value="0">
<h2>Результат и расход API</h2><pre>SUMMARY</pre><p>* — партия не завершена. Лимит ходов не считается ничьей. Это одна демонстрационная партия, не оценка рейтинга.</p><p><a href="game.pgn">Скачать PGN</a> · <a href="game.json">Запросы и ответы</a></p>
<script>const data=PACKED;let i=0,timer=null;const slider=document.getElementById('slider');slider.max=data.boards.length-1;
function draw(){document.getElementById('board').innerHTML=data.boards[i];document.getElementById('label').textContent=data.labels[i];slider.value=i;}
function stop(){clearInterval(timer);timer=null;document.getElementById('play').textContent='▶ Воспроизвести';}
document.getElementById('prev').onclick=()=>{stop();i=Math.max(0,i-1);draw();};document.getElementById('next').onclick=()=>{stop();i=Math.min(data.boards.length-1,i+1);draw();};slider.oninput=()=>{stop();i=Number(slider.value);draw();};
document.getElementById('play').onclick=()=>{if(timer){stop();return;}if(i===data.boards.length-1)i=0;document.getElementById('play').textContent='Пауза';draw();timer=setInterval(()=>{if(i>=data.boards.length-1){stop();return;}i++;draw();},900);};draw();</script></html>'''
    page = page.replace('SUMMARY', html.escape(json.dumps(summary, ensure_ascii=False, indent=2))).replace('PACKED', packed)
    if assisted:
        page = page.replace('Jev получает позицию и все легальные ходы. Подсказок от движка нет.',
                            'Jev выбирает среди ходов после локальной проверки взятий и разменов. Программа отсекает обнаруженные потери материала и мат соперника в один ход. Это Jev с тактической помощью; дальние угрозы могут быть пропущены.')
    if game.headers.get('JevPolicy') == 'engine':
        start = page.index('<h1>')
        end = page.index('<div id="board">')
        page = page[:start] + '<h1>Jev + Stockfish — белые</h1><p>Соперник: ' + html.escape(game.headers['Black']) + '</p><p>Stockfish рассчитывает до пяти кандидатов. Jev выбирает среди ходов не хуже лучшего более чем на 0.4 пешки по текущему анализу. При перевесе фильтр предпочитает варианты без повторений. Основную шахматную силу обеспечивает движок; это не рейтинг Jev.</p>' + page[end:]
    (folder / 'replay.html').write_text(page, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-turns', type=int, default=60, help='Maximum Jev turns/API calls')
    parser.add_argument('--model', default='typesafe/jev-1.13')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--policy', choices=['raw', 'guarded', 'engine'], default=DEFAULT_POLICY,
                        help='raw: Jev alone (default); guarded/engine: archived assisted experiments')
    parser.add_argument('--opponent', choices=['simple', 'stockfish'], default='simple')
    parser.add_argument('--skill', type=int, default=5, help='Stockfish opponent Skill Level, 0..20 (not Elo)')
    parser.add_argument('--engine-path')
    parser.add_argument('--coach-nodes', type=int, default=100000)
    parser.add_argument('--opponent-nodes', type=int, default=15000)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 200:
        parser.error('--max-turns must be between 1 and 200')
    if not 0 <= args.skill <= 20 or args.coach_nodes <= 0 or args.opponent_nodes <= 0:
        parser.error('skill must be 0..20; node budgets must be positive')
    board = chess.Board()
    if args.dry_run:
        if args.policy == 'engine':
            with chess.engine.SimpleEngine.popen_uci(engine_path(args.engine_path)) as coach:
                coach.configure({'Threads': 1, 'Hash': 64})
                safety = engine_filter(board, coach, args.coach_nodes)
        else:
            safety = filter_moves(board) if args.policy == 'guarded' else None
        print(json.dumps(make_request(board, args.model, safety), ensure_ascii=False, indent=2))
        return
    key = jev.read_key()
    folder = args.out or jev.ROOT / 'results' / ('chess-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    game = chess.pgn.Game()
    game.headers.update(Event='Jev demo', White=args.model, Black='Local material depth 2', Date=datetime.date.today().strftime('%Y.%m.%d'))
    game.headers['JevPolicy'] = args.policy
    game.headers['PromptVersion'] = PURE_PROMPT_VERSION if args.policy == 'raw' else 'assisted-with-san-history'
    game.headers['CoachNodes'] = str(args.coach_nodes)
    game.headers['OpponentNodes'] = str(args.opponent_nodes)
    node = game
    records, requests = [], []
    reason = 'running'
    coach = opponent = None
    print(f'Лимит: {args.max_turns} запросов. Отчёт: {folder / "replay.html"}', flush=True)
    save(folder, game, board, records, requests, reason, args.model, args.seed)
    try:
        if args.policy == 'engine':
            coach = chess.engine.SimpleEngine.popen_uci(engine_path(args.engine_path))
            coach.configure({'Threads': 1, 'Hash': 64, 'Skill Level': 20})
            game.headers['Coach'] = coach.id.get('name', 'Stockfish')
        if args.opponent == 'stockfish':
            opponent = chess.engine.SimpleEngine.popen_uci(engine_path(args.engine_path))
            opponent.configure({'Threads': 1, 'Hash': 64, 'Skill Level': args.skill})
            game.headers['Black'] = f'{opponent.id.get("name", "Stockfish")} Skill {args.skill}, {args.opponent_nodes} nodes'
        for ply in range(args.max_turns * 2):
            outcome = board.outcome(claim_draw=True)
            if outcome:
                reason = outcome.termination.name
                break
            start = time.monotonic()
            actor = 'Jev' if board.turn == chess.WHITE else 'Локальный бот'
            safety = None
            if board.turn == chess.WHITE:
                safety = engine_filter(board, coach, args.coach_nodes) if coach else (filter_moves(board) if args.policy == 'guarded' else None)
                body = make_request(board, args.model, safety)
                attempt = {'request': body, 'safety': safety}
                requests.append(attempt)
                response = jev.request(body, key, 45)
                attempt['response'] = response
                answer = response['answers'].get('move', {})
                move_id = answer.get('choice')
                if move_id not in body['questions']['move']['criteria']:
                    raise ValueError('API returned a move outside the legal move list')
                move = chess.Move.from_uci(move_id)
            else:
                move = opponent.play(board, chess.engine.Limit(nodes=args.opponent_nodes)).move if opponent else opponent_move(board, rng)
                if opponent:
                    actor = 'Stockfish'
            san, before = board.san(move), board.fen()
            board.push(move)
            node = node.add_variation(move)
            elapsed = time.monotonic() - start
            records.append(dict(ply=ply+1, actor=actor, uci=move.uci(), san=san, fen_before=before, fen_after=board.fen(), elapsed_s=elapsed, safety=safety))
            print(f'{ply+1:3}. {actor}: {san} [{move.uci()}], {elapsed:.2f} с', flush=True)
            save(folder, game, board, records, requests, reason, args.model, args.seed)
        outcome = board.outcome(claim_draw=True)
        reason = outcome.termination.name if outcome else 'move_limit; unfinished'
    except KeyboardInterrupt:
        reason = 'interrupted; unfinished'
    except Exception as exc:
        reason = 'error: ' + str(exc).replace(key, '[REDACTED]')
    finally:
        for engine in (coach, opponent):
            if engine:
                engine.quit()
        stats = save(folder, game, board, records, requests, reason, args.model, args.seed)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print(f'Просмотр: {folder / "replay.html"}')
    return 1 if reason.startswith('error:') else 0


if __name__ == '__main__':
    raise SystemExit(main())
