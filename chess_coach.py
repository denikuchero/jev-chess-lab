"""Stockfish-assisted candidate selection. Jev chooses among engine-approved moves."""
from pathlib import Path
import shutil
import chess
import chess.engine


def engine_path(explicit=None):
    path = explicit or shutil.which('stockfish')
    if path:
        return path
    local = Path(__file__).resolve().parent / '.tools/stockfish/extracted/usr/games/stockfish'
    if local.exists():
        return str(local)
    raise ValueError('Stockfish not found. Supply --engine-path PATH (see README).')


def repeat_info(board, move):
    board.push(move)
    try:
        return {'repeats_position': board.is_repetition(2),
                'opponent_can_claim_draw': board.can_claim_draw(),
                'automatic_draw': board.is_game_over() and not board.is_checkmate()}
    finally:
        board.pop()


def choose_candidates(rows, margin_cp=40):
    """Keep near-best engine candidates, avoiding repetition when winning if possible."""
    best = max(row['score_cp'] for row in rows)
    if best > 90000:
        pool = [r for r in rows if r['score_cp'] == best]
    else:
        pool = [r for r in rows if r['score_cp'] >= best-margin_cp]
    reason = 'near_best_engine_moves'
    if best >= 150:
        progress = [r for r in pool if not any(r[k] for k in ('repeats_position', 'opponent_can_claim_draw', 'automatic_draw'))]
        if progress:
            pool = progress
            reason += '; avoid_repetition_while_winning'
    return pool, reason


def engine_filter(board, engine, nodes=100000):
    legal_count = board.legal_moves.count()
    infos = engine.analyse(board, chess.engine.Limit(nodes=nodes), multipv=min(5, legal_count))
    rows = []
    for info in infos:
        move = info['pv'][0]
        score = info['score'].pov(board.turn)
        rows.append(dict(uci=move.uci(), san=board.san(move), score_cp=score.score(mate_score=100000),
                         mate_in=score.mate(), depth=info.get('depth'), pv=[m.uci() for m in info['pv'][:8]],
                         **repeat_info(board, move)))
    selected, reason = choose_candidates(rows)
    return dict(kind='engine', engine=engine.id, nodes_budget=nodes, candidates=rows,
                offered=[r['uci'] for r in selected], reason=reason,
                legal_count=legal_count, excluded_count=legal_count-len(selected))
