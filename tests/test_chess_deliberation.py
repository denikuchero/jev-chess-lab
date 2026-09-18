import unittest
import chess
from chess_demo import make_request
from chess_deliberation import deliberate, RequestBudgetExceeded


def answer_choice(options):
    keys=list(options)
    return dict(choice=keys[0],probabilities={k:(1 if i==0 else 0) for i,k in enumerate(keys)})


class DeliberationTests(unittest.TestCase):
    def run_loop(self,unsafe=False,budget=20):
        board=chess.Board(); body=make_request(board,'test'); records=[]
        def request(b):
            if 'loses_material' in b['questions']:
                return {'answers':{'loses_material':{'noul':0.9 if unsafe else 0.1},
                                   'allows_mate':{'noul':0.01},'quality':{'score':2},
                                   'black_reply':answer_choice(b['questions']['black_reply']['criteria'])}}
            return {'answers':{'move':answer_choice(b['questions']['move']['criteria'])}}
        move,review=deliberate(board,body,request,records,budget)
        self.assertEqual(board.fen(),chess.STARTING_FEN)
        self.assertIn(move,board.legal_moves)
        return records,review

    def test_five_calls_without_retry(self):
        records,review=self.run_loop()
        self.assertEqual(len(records),5)
        self.assertEqual([r['stage'] for r in records],['proposal','review','review','review','selection'])
        self.assertFalse(review['retried'])

    def test_model_rejection_triggers_three_new_reviews(self):
        records,review=self.run_loop(unsafe=True)
        self.assertEqual(len(records),8)
        self.assertTrue(review['retried'])
        self.assertEqual(len({r['uci'] for r in review['reviews']}),6)

    def test_budget_retains_final_selection(self):
        records,_=self.run_loop(unsafe=True,budget=5)
        self.assertEqual(len(records),5)
        with self.assertRaises(RequestBudgetExceeded):self.run_loop(budget=4)

    def test_failure_is_retained(self):
        records=[]
        def fail(body):raise ValueError('simulated failure')
        with self.assertRaises(ValueError):
            deliberate(chess.Board(),make_request(chess.Board(),'test'),fail,records)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['error'],'simulated failure')
