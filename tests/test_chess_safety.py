import unittest
import chess
from chess_safety import assess, filter_moves


class SafetyTests(unittest.TestCase):
    def test_first_game_blunders_excluded(self):
        examples = [
            ('rnbqkbnr/1pp3pp/3ppp2/p7/3PP3/2N2N2/PPP2PPP/R1BQKB1R w KQkq - 0 5', 'c3d5'),
            ('rnbq1b1r/1pp3p1/8/pB1Pkp1p/8/8/PPP2PPP/R2Q1RK1 w - - 0 13', 'd1h5'),
            ('rn6/1pp3p1/8/pB3br1/4k3/8/PPP2PPP/3R2K1 w - - 4 21', 'd1d4')]
        for fen, move in examples:
            board = chess.Board(fen)
            result = filter_moves(board)
            self.assertNotIn(move, result['offered'])
            self.assertEqual(board.fen(), fen)

    def test_fair_exchange_allowed(self):
        board = chess.Board('4k3/8/4p3/3p4/4P3/8/8/4K3 w - - 0 1')
        self.assertEqual(assess(board, chess.Move.from_uci('e4d5'))['material_delta_cp'], 0)

    def test_en_passant_hanging_pawn(self):
        board = chess.Board('4k3/8/8/8/3p4/8/4P3/4K3 w - - 0 1')
        self.assertEqual(assess(board, chess.Move.from_uci('e2e4'))['material_delta_cp'], -100)

    def test_opponent_mate_rejected(self):
        board = chess.Board()
        board.push_san('f3'); board.push_san('e5')
        self.assertTrue(assess(board, chess.Move.from_uci('g2g4'))['allows_mate'])
        self.assertNotIn('g2g4', filter_moves(board)['offered'])

    def test_mate_prioritized(self):
        board = chess.Board()
        for san in ['e4','e5','Qh5','Nc6','Bc4','Nf6']:
            board.push_san(san)
        self.assertEqual(filter_moves(board)['offered'], ['h5f7'])
