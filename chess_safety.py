"""Bounded legal same-square exchange analysis, not a full chess engine."""
import chess

VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def material(board, color):
    return sum(v * (len(board.pieces(p, color)) - len(board.pieces(p, not color)))
               for p, v in VALUES.items())


def exchange(board, square, color, depth=6):
    """Both sides may decline a recapture. Ignores off-square tactics/check evasions.

    This is an exchange estimate, not a guaranteed minimax position value.
    Legal captures automatically exclude pinned or otherwise illegal recaptures.
    """
    values = [material(board, color)]
    if depth:
        for move in list(board.generate_legal_captures()):
            if move.to_square != square:
                continue
            board.push(move)
            values.append(exchange(board, square, color, depth - 1))
            board.pop()
    return (max if board.turn == color else min)(values)


def assess(board, move):
    color = board.turn
    initial = material(board, color)
    board.push(move)
    try:
        if board.is_checkmate():
            return dict(mate_now=True, allows_mate=False, material_delta_cp=0, reply=None)
        worst = material(board, color)
        reply = None
        for response in list(board.legal_moves):
            capture = board.is_capture(response)
            board.push(response)
            try:
                if board.is_checkmate():
                    return dict(mate_now=False, allows_mate=True, material_delta_cp=-100000, reply=response.uci())
                if capture:
                    value = exchange(board, response.to_square, color)
                    if value < worst:
                        worst, reply = value, response.uci()
            finally:
                board.pop()
        return dict(mate_now=False, allows_mate=False, material_delta_cp=worst-initial, reply=reply)
    finally:
        board.pop()


def filter_moves(board):
    analysis = {m.uci(): assess(board, m) for m in sorted(board.legal_moves, key=lambda m: m.uci())}
    mates = [k for k, v in analysis.items() if v['mate_now']]
    survivors = [k for k, v in analysis.items() if not v['allows_mate']]
    safe = [k for k in survivors if analysis[k]['material_delta_cp'] >= 0]
    if mates:
        offered, reason = mates, 'mate_in_one'
    elif safe:
        offered, reason = safe, 'no_detected_immediate_material_loss'
    else:
        pool = survivors or list(analysis)
        best = max(analysis[k]['material_delta_cp'] for k in pool)
        offered = [k for k in pool if analysis[k]['material_delta_cp'] == best]
        reason = 'least_estimated_loss; no_safe_move_found'
    return dict(analysis=analysis, offered=offered, reason=reason,
                legal_count=len(analysis), excluded_count=len(analysis)-len(offered))
