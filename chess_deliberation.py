"""Jev proposes, critiques and selects. No engine or numeric chess evaluator."""
from concurrent.futures import ThreadPoolExecutor
import copy
import math
import time

import chess

from jev import probability


class RequestBudgetExceeded(RuntimeError):
    pass


def validate_choice(answer, options):
    choice=answer.get('choice')
    probs=answer.get('probabilities')
    if choice not in options or not isinstance(probs,dict) or set(probs)!=set(options):
        raise ValueError('Invalid choice or incomplete probability distribution')
    for p in probs.values(): probability(p)
    if abs(sum(probs.values())-1)>0.02 or probs[choice]+1e-6<max(probs.values()):
        raise ValueError('Invalid choice probability distribution')
    return probs


def question(kind,instructions,criteria=None):
    q=dict(type=kind,instructions=instructions)
    if criteria is not None:q['criteria']=criteria
    return q


def snapshot(board):
    return dict(fen=board.fen(),side_to_move='white' if board.turn else 'black',
                pieces={chess.square_name(s):('white ' if p.color else 'black ')+chess.piece_name(p.piece_type)
                        for s,p in sorted(board.piece_map().items())},
                in_check=board.is_check(),terminal=board.is_game_over(claim_draw=True))


def review_body(board,uci,model):
    child=board.copy(); move=chess.Move.from_uci(uci); san=child.san(move); child.push(move)
    state=dict(before=snapshot(board),candidate=dict(uci=uci,san=san),after=snapshot(child))
    questions={
        'loses_material': question('noul','After the candidate White move, can Black force a net material gain soon? Check legal captures, recaptures and attacks on ALL White pieces. A fair exchange is not a loss; merely being attacked is not enough. Assess from White perspective.'),
        'allows_mate': question('noul','Can Black checkmate White on its very next move in the after position?'),
        'quality': question('score','Evaluate the candidate for White after Black responds strongly. Do not reward a check that sacrifices material without compensation.',
                            ['Losing blunder','Dubious or wasteful','Playable','Good','Winning or excellent'])}
    if not child.is_game_over(claim_draw=True):
        questions['black_reply']=question('choice','Play Black in the AFTER position. Choose the strongest reply to punish White, especially hanging pieces or checkmate. Every offered move is legal.',
                                         {m.uci():child.san(m) for m in sorted(child.legal_moves,key=lambda m:m.uci())})
    return dict(model=model,state=state,questions=questions)


def deliberate(board, proposal, request_fn, records, max_calls=300):
    """5 calls normally, up to 8 if the first three candidates all fail review.

    Only Jev probabilities rank candidates and assess risk. Hypothetical board
    transitions are legal-rule operations, not tactical evaluations.
    """
    def reserve(n):
        if len(records)+n>max_calls:
            raise RequestBudgetExceeded('API request budget reached; unfinished')

    def call(body,stage,uci=None):
        entry=dict(stage=stage,candidate=uci,request=body,safety=None,turn=board.fullmove_number)
        records.append(entry)
        started=time.monotonic()
        try:
            entry['response']=request_fn(body)
        except Exception as exc:
            entry['error']=str(exc)
            raise
        finally:
            entry['elapsed_s']=time.monotonic()-started
        return entry

    # Reserve enough for proposal + three reviews + final before starting a turn.
    legal=proposal['questions']['move']['criteria']
    reserve(2+min(3,len(legal)))
    first=call(proposal,'proposal')
    probs=validate_choice(first['response']['answers'].get('move',{}),legal)
    ranked=sorted(legal,key=lambda u:(-probs[u],u))
    reviews=[]

    def review_group(candidates):
        # Append placeholders before dispatch, so failed calls still count and are retained.
        slots=[dict(stage='review',candidate=uci,request=review_body(board,uci,proposal['model']),safety=None,turn=board.fullmove_number) for uci in candidates]
        records.extend(slots)
        def work(slot):
            started=time.monotonic()
            try:
                slot['response']=request_fn(slot['request'])
            except Exception as exc:
                slot['error']=str(exc)
            finally:slot['elapsed_s']=time.monotonic()-started
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work,slots))
        for slot in slots:
            if 'error' in slot:raise ValueError('Review request failed: '+slot['error'])
            ans=slot['response']['answers']
            loss=probability(ans['loses_material']['noul']); mate=probability(ans['allows_mate']['noul'])
            quality=ans['quality']['score']
            if isinstance(quality,bool) or not isinstance(quality,(int,float)) or not math.isfinite(quality) or not 0<=quality<=4:
                raise ValueError('Invalid review quality score')
            uci=slot['candidate']; child=board.copy(); child.push_uci(uci)
            reply=None
            if 'black_reply' in slot['request']['questions']:
                validate_choice(ans['black_reply'],slot['request']['questions']['black_reply']['criteria'])
                reply=ans['black_reply']['choice']; child.push_uci(reply)
            reviews.append(dict(uci=uci,proposal_probability=probs[uci],loss_probability=loss,
                                mate_probability=mate,quality=quality,black_reply=reply,
                                after_model_reply=snapshot(child)))

    initial=ranked[:3]
    review_group(initial)
    safe=lambda r:r['loss_probability']<0.65 and r['mate_probability']<0.65
    retry=ranked[3:6]
    if not any(map(safe,reviews)) and retry and len(records)+len(retry)+1<=max_calls:
        review_group(retry)
    accepted=[r for r in reviews if safe(r)] or reviews
    final=dict(model=proposal['model'],state=dict(position=copy.deepcopy(proposal['state']),reviews=reviews,
                                               warning='Reviews and Black replies are fallible predictions of the same Jev model, not verified engine evaluations.'),
               questions={'move':question('choice','Choose the best White move from the reviewed candidates. Independently check each hypothetical Black reply, captures and recaptures. Prefer avoiding material loss, then useful progress and king safety. Reviews may be wrong. A high proposal probability does not prove a good move.',
                                         {r['uci']:legal[r['uci']] for r in accepted})})
    reserve(1)
    last=call(final,'selection')
    validate_choice(last['response']['answers'].get('move',{}),final['questions']['move']['criteria'])
    chosen=last['response']['answers']['move']['choice']
    return chess.Move.from_uci(chosen),dict(proposal_top=ranked[0],reviews=reviews,offered=list(final['questions']['move']['criteria']),selected=chosen,
                                          changed=chosen!=ranked[0],retried=len(reviews)>len(initial))
