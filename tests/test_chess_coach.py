import unittest
import chess
import chess.engine
from chess_coach import choose_candidates, repeat_info, engine_filter, engine_path


def candidate(uci, score, repeats=False):
    return dict(uci=uci, score_cp=score, repeats_position=repeats,
                opponent_can_claim_draw=False, automatic_draw=False)


class CoachTests(unittest.TestCase):
    def test_repetition_filtered_when_winning(self):
        rows, reason = choose_candidates([candidate('a', 300, True), candidate('b', 275)])
        self.assertEqual([r['uci'] for r in rows], ['b'])

    def test_draw_not_rejected_when_losing(self):
        rows, _ = choose_candidates([candidate('draw', 0, True), candidate('loss', -500)])
        self.assertEqual([r['uci'] for r in rows], ['draw'])

    def test_does_not_throw_away_advantage_to_avoid_repeat(self):
        rows, _ = choose_candidates([candidate('a', 300, True), candidate('b', -100)])
        self.assertEqual(rows[0]['uci'], 'a')

    def test_actual_history_recognized_and_preserved(self):
        board = chess.Board()
        for move in ['g1f3','g8f6','f3g1']:
            board.push_uci(move)
        before = board.fen(), list(board.move_stack)
        info = repeat_info(board, chess.Move.from_uci('f6g8'))
        self.assertTrue(info['repeats_position'])
        self.assertEqual((board.fen(), board.move_stack), before)

    def test_real_engine_mate_and_board_preserved(self):
        try:
            path = engine_path()
        except ValueError:
            self.skipTest('Optional Stockfish executable not installed')
        board = chess.Board()
        for san in ['e4','e5','Qh5','Nc6','Bc4','Nf6']:
            board.push_san(san)
        before = board.fen()
        with chess.engine.SimpleEngine.popen_uci(path) as engine:
            engine.configure({'Threads':1,'Hash':16})
            result = engine_filter(board, engine, 10000)
        self.assertEqual(result['offered'], ['h5f7'])
        self.assertEqual(board.fen(), before)
