# -*- coding : utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""
from abc import ABC, abstractmethod
# from util.context_tricks import nop_context


class EventRule(ABC):
    """A rule defining when a scheduled function should execute.
    """
    # Instances of EventRule are assigned a calendar instance when scheduling
    # a function.
    _cal = None

    @property
    def cal(self):
        return self._cal

    @cal.setter
    def cal(self, value):
        self._cal = value

    @abstractmethod
    def should_trigger(self, dt):
        """
        Checks if the rule should trigger with its current state.
        This method should be pure and NOT mutate any state on the object.
        """
        raise NotImplementedError('should_trigger')


class StatelessRule(EventRule):
    """
    A stateless rule has no observable side effects.
    This is reentrant and will always give the same result for the
    same datetime.
    Because these are pure, they can be composed to create new rules.
    """
    def should_trigger(self, dt):
        raise NotImplementedError

    def and_(self, rule):
        """
        Logical and of two rules, triggers only when both rules trigger.
        This follows the short circuiting rules for normal and.
        """
        return ComposedRule(self, rule, ComposedRule.lazy_and)
    __and__ = and_


class ComposedRule(StatelessRule):
    """
    A rule that composes the results of two rules with some composing function.
    The composing function should be a binary function that accepts the results
    first(dt) and second(dt) as positional arguments.
    For example, operator.and_.
    If lazy=True, then the lazy composer is used instead. The lazy composer
    expects a function that takes the two should_trigger functions and the
    datetime. This is useful of you don't always want to call should_trigger
    for one of the rules. For example, this is used to implement the & and |
    operators so that they will have the same short circuit logic that is
    expected.
    """
    def __init__(self, first, second, composer):
        if not (isinstance(first, StatelessRule) and
                isinstance(second, StatelessRule)):
            raise ValueError('Only two StatelessRules can be composed')

        self.first = first
        self.second = second
        self.composer = composer

    def should_trigger(self, dt):
        """
        Composes the two rules with a lazy composer.
        """
        return self.composer(
            self.first.should_trigger,
            self.second.should_trigger,
            dt
        )

    @staticmethod
    def lazy_and(first_should_trigger, second_should_trigger, dt):
        """
        Lazily ands the two rules. This will NOT call the should_trigger of the
        second rule if the first one returns False.
        """
        return first_should_trigger(dt) and second_should_trigger(dt)

    @property
    def cal(self):
        return self.first.cal

    @cal.setter
    def cal(self, value):
        # Thread the calendar through to the underlying rules.
        self.first.cal = self.second.cal = value


class Always(StatelessRule):
    """
    A rule that always triggers.
    """
    @staticmethod
    def always_trigger(dt):
        """
        A should_trigger implementation that will always trigger.
        """
        return True
    should_trigger = always_trigger


class Never(StatelessRule):
    """
    A rule that never triggers.
    """
    @staticmethod
    def never_trigger(dt):
        """
        A should_trigger implementation that will never trigger.
        """
        return False
    should_trigger = never_trigger

