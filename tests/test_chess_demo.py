import unittest
import random
import chess
from chess_demo import make_request, opponent_move
from unittest.mock import patch
import tempfile
from pathlib import Path
import io
import json
import chess_demo


class ChessDemoTests(unittest.TestCase):
    def test_explicit_pieces_are_observations_not_filtered_moves(self):
        board=chess.Board()
        body=make_request(board,'test')
        self.assertEqual(body['state']['pieces']['white']['d1'],'queen')
        self.assertEqual(body['state']['pieces']['black']['e8'],'king')
        self.assertEqual(sum(len(v) for v in body['state']['pieces'].values()),32)
        self.assertIn('knight g1 to f3',body['questions']['move']['criteria']['g1f3'])
        self.assertEqual(list(body['questions']['move']['criteria']),sorted(m.uci() for m in board.legal_moves))

    def test_default_game_never_uses_coach_or_filter(self):
        def reply(body, key, timeout):
            self.assertEqual(set(body['questions']['move']['criteria']), {m.uci() for m in chess.Board().legal_moves})
            self.assertNotIn('tactical_filter', body['state'])
            self.assertNotIn('engine_coach', body['state'])
            return {'answers': {'move': {'choice': 'e2e4'}}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'game'
            with patch('sys.argv', ['chess_demo.py','--max-turns','1','--out',str(out)]), \
                 patch('chess_demo.jev.read_key',return_value='fake'), \
                 patch('chess_demo.jev.request',side_effect=reply), \
                 patch('chess_demo.filter_moves',side_effect=AssertionError('No tactical help allowed')), \
                 patch('chess_demo.engine_filter',side_effect=AssertionError('No engine help allowed')), \
                 patch('chess.engine.SimpleEngine.popen_uci',side_effect=AssertionError('No Stockfish allowed')), \
                 patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(chess_demo.main(),0)
            data=json.loads((out/'game.json').read_text())
            self.assertEqual(data['policy'],'raw')
            self.assertIsNone(data['requests'][0]['safety'])

    def test_history_is_complete_in_both_notations(self):
        board=chess.Board()
        for san in ['e4','e5','Nf3','Nc6']:
            board.push_san(san)
        state=make_request(board,'test')['state']
        self.assertEqual(state['history_san'],['e4','e5','Nf3','Nc6'])
        self.assertEqual(state['history_uci'],['e2e4','e7e5','g1f3','b8c6'])

    def test_all_legal_moves_offered(self):
        board = chess.Board()
        body = make_request(board, 'test')
        self.assertEqual(set(body['questions']['move']['criteria']), {m.uci() for m in board.legal_moves})

    def test_opponent_finds_mate_and_preserves_board(self):
        board = chess.Board()
        for san in ['f3', 'e5', 'g4']:
            board.push_san(san)
        before = board.fen()
        move = opponent_move(board, random.Random(42))
        self.assertEqual(board.fen(), before)
        self.assertEqual(move.uci(), 'd8h4')
        board.push(move)
        self.assertTrue(board.is_checkmate())

    def test_special_moves_offered(self):
        for fen, expected in [('4k3/P7/8/8/8/8/8/4K3 w - - 0 1', 'a7a8q'),
                              ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1', 'e1g1')]:
            self.assertIn(expected, make_request(chess.Board(fen), 'test')['questions']['move']['criteria'])
