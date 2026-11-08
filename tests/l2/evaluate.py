"""Offline scoring. Run with PYTHONPATH=plugin python3 tests/l2/evaluate.py.

Labels never reach the detector or canned adapter. Fake answers are literal
scripted responses, not measurements of Hermes or any model's quality.
"""
import hashlib
import json
from pathlib import Path
from line.detect import HoldTracker, Utterance, LABELS

ROOT = Path(__file__).resolve().parents[2]
# Explicit canned answers exercise the injected-model protocol only.
CANNED_CONTINGENT = frozenset([
    'How can I help with your order?', 'This is the front desk speaking.',
    'Yes, I heard you asking for scheduling.', 'Speaking, how may I assist?',
    'I can look into the reservation for you.', 'Sorry, were you asking about a refund?',
    'Please ho—sorry, I can help with your request now.',
])

class FakeModel:
    def __init__(self): self.calls = 0
    def __call__(self, prompt):
        self.calls += 1
        turns=json.loads(prompt)['turns']
        human=turns[-1]['text'] in CANNED_CONTINGENT and len(turns)>1
        return json.dumps(dict(label='human' if human else 'unknown',confidence='high' if human else 'low',
                               evidence=[len(turns)-2,len(turns)-1] if human else [len(turns)-1],contingent=human))


def evaluate(split, with_model=False):
    path=ROOT/'fixtures'/'hold-sim'/f'{split}.json'
    corpus=json.loads(path.read_text())
    matrix={label:{prediction:0 for prediction in LABELS} for label in LABELS}
    model=FakeModel() if with_model else None
    false=missed_events=missed_episodes=human_episodes=0
    delays=[]; predictions=[]; negative_ms=0
    for stream in corpus['streams']:
        tracker=HoldTracker(model)
        onset=stream['human_onset_ms']
        negative_ms += stream['duration_ms'] if onset is None else onset
        detected=False
        for event in stream['events']:
            if event['type'] not in ('prompt','interrupt'): continue
            result=tracker.feed(Utterance(**{k:event[k] for k in ('text','at_ms','speaker','last','turn_id')}))
            if event['speaker']!='business' or not event['last']: continue
            label=event['label']; matrix[label][result.label]+=1
            false+=result.label=='human' and label!='human'
            missed_events+=label=='human' and result.label!='human'
            if label=='human' and result.label=='human' and not detected:
                detected=True; delays.append(event['at_ms']-onset)
            predictions.append([stream['id'],event['turn_id'],label,result.label])
        if onset is not None:
            human_episodes+=1
            missed_episodes+=not detected
    total=sum(sum(row.values()) for row in matrix.values())
    negatives=sum(sum(row.values()) for label,row in matrix.items() if label!='human')
    return dict(split=split,mode='fake model' if with_model else 'no model',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                streams=len(corpus['streams']),events=total,matrix=matrix,false_human=false,negative_events=negatives,
                false_human_rate=false/negatives if negatives else 0,missed_human_events=missed_events,
                missed_human_episodes=missed_episodes,human_episodes=human_episodes,
                unknown_fraction=sum(row['unknown'] for row in matrix.values())/total,
                negative_exposure_hours=negative_ms/3600000,false_per_negative_hour=false/(negative_ms/3600000),
                detection_delays_ms=delays,model_calls=model.calls if model else 0,predictions=predictions)

if __name__=='__main__':
    for split in ('train','held-out','soak'):
        for mode in (False,True):
            result=evaluate(split,mode)
            result.pop('predictions')
            print(json.dumps(result,sort_keys=True))
