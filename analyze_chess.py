"""Audit recorded games using the same limited tactical detector, offline."""
import argparse
import json
from pathlib import Path
import chess
from chess_safety import filter_moves, material


def audit(path):
    data = json.loads(path.read_text())
    mistakes = []
    risks = []
    for row in data['moves']:
        if row['actor'] != 'Jev':
            continue
        board = chess.Board(row['fen_before'])
        safety = row.get('safety')
        if not safety or 'analysis' not in safety:
            safety = filter_moves(board)
        selected = safety['analysis'][row['uci']]
        if selected['material_delta_cp'] < 0 or selected['allows_mate']:
            item = dict(turn=(row['ply']+1)//2, san=row['san'], uci=row['uci'],
                        estimated_loss_pawns=None if selected['allows_mate'] else -selected['material_delta_cp']/100,
                        allows_mate_in_one=selected['allows_mate'],
                        opponent_reply=selected['reply'], avoidable=row['uci'] not in safety['offered'])
            risks.append(item)
            if item['avoidable']:
                mistakes.append(item)
    final = chess.Board(data['moves'][-1]['fen_after'])
    return dict(file=str(path), result=data['summary']['result'], reason=data['summary']['reason'],
                jev_turns=sum(r['actor']=='Jev' for r in data['moves']),
                avoidable_immediate_losses=len(mistakes), risky_moves=risks,
                avoidable_losses_at_least_one_pawn=sum(x['allows_mate_in_one'] or x['estimated_loss_pawns'] >= 1 for x in mistakes),
                final_white_material_advantage_pawns=material(final,chess.WHITE)/100,
                limitations='Same-square exchange estimate, not Stockfish or independent strength evaluation. No long tactics. Different positions across games.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('games',type=Path,nargs='+')
    args=parser.parse_args()
    print(json.dumps([audit(p) for p in args.games],ensure_ascii=False,indent=2))
