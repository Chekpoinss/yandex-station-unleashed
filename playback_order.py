"""Playback order/history shared by local files and SoundCloud, without I/O."""
from random import SystemRandom


def unique(values):
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(v for v in values if isinstance(v, str)))[:2000]


class PlaybackOrder:
    def __init__(self, tracks=(), current=None, shuffler=None):
        self.base = unique(tracks)
        self.order = list(self.base)
        self.current = current if current in self.base else None
        self.history = [self.current] if self.current else []
        self.history_index = len(self.history) - 1
        self.visited = list(self.history)
        self.shuffle = False
        self.repeat = 'None'
        self.revision = 0
        self.shuffler = shuffler or SystemRandom().shuffle

    def dump(self):
        return {'version': 1, 'base': list(self.base), 'order': list(self.order),
                'current': self.current, 'history': list(self.history),
                'history_index': self.history_index, 'visited': list(self.visited),
                'shuffle': self.shuffle, 'repeat': self.repeat, 'revision': self.revision}

    @classmethod
    def restore(cls, saved, valid, fallback=(), current=None, shuffler=None):
        valid = set(valid)
        if not isinstance(saved, dict) or saved.get('version') != 1:
            return cls([i for i in fallback if i in valid], current, shuffler)
        clean = lambda key: [i for i in unique(saved.get(key, [])) if i in valid]
        result = cls(clean('base'), saved.get('current'), shuffler)
        members = set(result.base)
        result.order = [i for i in clean('order') if i in members]
        result.order += [i for i in result.base if i not in result.order]
        history = saved.get('history', [])
        result.history = [i for i in history if isinstance(i, str) and i in members][-4000:] if isinstance(history, list) else []
        index = saved.get('history_index', len(result.history) - 1)
        result.history_index = max(-1, min(index if isinstance(index, int) else -1, len(result.history) - 1))
        if result.current and (result.history_index < 0 or result.history[result.history_index] != result.current):
            result.history.append(result.current)
            result.history_index = len(result.history) - 1
        result.visited = [i for i in clean('visited') if i in members]
        if result.current and result.current not in result.visited:
            result.visited.append(result.current)
        result.shuffle = bool(saved.get('shuffle'))
        result.repeat = saved.get('repeat') if saved.get('repeat') in ('None', 'All', 'One') else 'None'
        result.revision = saved.get('revision', 0) if isinstance(saved.get('revision'), int) else 0
        if not result.shuffle:
            result.order = list(result.base)
        return result

    def copy(self):
        return self.restore(self.dump(), self.base, shuffler=self.shuffler)

    def discard(self, ident):
        before=self.history[:self.history_index+1]
        self.base=[i for i in self.base if i!=ident]
        self.order=[i for i in self.order if i!=ident]
        self.history=[i for i in self.history if i!=ident]
        self.history_index=len([i for i in before if i!=ident])-1
        self.visited=[i for i in self.visited if i!=ident]
        if self.current==ident:self.current=None
        self.revision+=1

    @property
    def index(self):
        return self.order.index(self.current) if self.current in self.order else -1

    def select(self, ident):
        if ident not in self.base:
            raise ValueError('Трека нет в очереди')
        if ident not in self.visited:
            self.visited.append(ident)
        if ident == self.current and self.history_index >= 0 and self.history[self.history_index] == ident:
            return ident
        self.current = ident
        self.history = self.history[:self.history_index + 1] + [ident]
        self.history = self.history[-4000:]
        self.history_index = len(self.history) - 1
        self.revision += 1
        return ident

    def set_shuffle(self, enabled):
        if self.shuffle == enabled:
            return
        self.shuffle = enabled
        if enabled:
            self._shuffle_remaining()
        else:
            self.order = list(self.base)
        self.revision += 1

    def _shuffle_remaining(self):
        prefix = [i for i in self.visited if i != self.current]
        if self.current:
            prefix.append(self.current)
        remaining = [i for i in self.base if i not in prefix]
        self.shuffler(remaining)
        self.order = prefix + remaining

    def append(self, ident):
        if ident in self.base:
            return
        if len(self.base) >= 2000:
            raise ValueError('Очередь содержит максимум 2000 треков')
        self.base.append(ident)
        if self.shuffle:
            self._shuffle_remaining()
        else:
            self.order = list(self.base)
        self.revision += 1

    def next(self, automatic=False):
        if automatic and self.repeat == 'One' and self.current:
            return self.current
        if self.history_index + 1 < len(self.history):
            self.history_index += 1
            self.current = self.history[self.history_index]
            self.revision += 1
            return self.current
        if self.shuffle:
            remaining = [i for i in self.order if i not in self.visited]
            if remaining:
                return self.select(remaining[0])
            if self.repeat != 'All' or not self.base:
                return None
            self.order = list(self.base)
            self.shuffler(self.order)
            if len(self.order) > 1 and self.order[0] == self.current:
                self.order[0], self.order[1] = self.order[1], self.order[0]
            self.visited = []
            target = self.order[0]
        else:
            index = self.index + 1
            if index < len(self.order):
                target = self.order[index]
            elif self.repeat == 'All' and self.order:
                target, self.visited = self.order[0], []
            else:
                return None
        return self.select(target)

    def previous(self):
        if self.history_index > 0:
            self.history_index -= 1
            self.current = self.history[self.history_index]
            self.revision += 1
            return self.current
        if self.shuffle or not self.order:
            return None
        index = self.index - 1
        if index < 0 and self.repeat != 'All':
            return None
        target = self.order[index]
        self.history = ([target] + self.history)[:4000]
        self.history_index = 0
        self.current = target
        if target not in self.visited:
            self.visited.append(target)
        self.revision += 1
        return target

    @property
    def has_next(self):
        return (self.history_index + 1 < len(self.history) or
                (any(i not in self.visited for i in self.order) if self.shuffle else self.index + 1 < len(self.order)) or
                (self.repeat == 'All' and bool(self.base)))

    @property
    def has_previous(self):
        return self.history_index > 0 or (not self.shuffle and bool(self.order) and (self.index > 0 or self.repeat == 'All'))
