"""Privacy-preserving, bounded voice follow-up session.
No automatic recording without deliberate user's enable action.
"""
import time,threading
class VoiceSession:
    def __init__(self,max_turns=5,timeout=90):
        self.lock=threading.RLock();self.max_turns=max_turns;self.timeout=timeout
        self.active=False;self.turns=0;self.expires=0
    def start(self):
        with self.lock:self.active=True;self.turns=0;self.expires=time.monotonic()+self.timeout
    def stop(self):
        with self.lock:self.active=False;self.turns=0;self.expires=0
    def consume(self):
        with self.lock:
            if not self.active or time.monotonic()>self.expires or self.turns>=self.max_turns:
                self.stop();return False
            self.turns+=1;self.expires=time.monotonic()+self.timeout
            return True
    def status(self):
        with self.lock:return {'active':self.active and time.monotonic()<self.expires and self.turns<self.max_turns,
            'turns':self.turns,'remaining':max(0,self.max_turns-self.turns)}
