0013 Assignment actions
***********************

Status
======

Accepted - October 2023

Context
=======
There are certain actions related to ``LearnerContentAssignment`` records
that we want to persist data about, but which don't strictly describe
the lifecycle of an assignment record.  For instance:

- For a new assignment, the learner of the assignment is linked to the related enterprise customer.
- A learner is notified of a new assignment via email.
- A learner is reminded about an allocated (unaccepted) assignment via email, perhaps
  multiple times.

Furthermore, our desired user experience requires that we be able to make queries like:

- What is the most recent successful action related to a given assignment?
- When did a given action fail, and why?
- Request an ordered list of all actions performed against an allocated assignment.

Decision
========
We'll introduce a model called ``LearnerContentAssignmentAction`` that, for a given assignment,
captures:

- Some choice field describing the type of action taken
- When the action was created and completed
- If there was an error, the type of error and a stacktrace

Consequences
============
Good consequences:

- It's easy to query for a "timeline" of a given assignment's actions, of any particular types.
- We have a natural and easy place to hook into for storing data about what happened
  in the case of failure during a particular action.
- Gives us a natural place from which to model the sub-domain of actions around an assignment.

Bad consequences:

- None

Alternatives Considered
=======================

More columns on the assignment model/table
------------------------------------------
This is undesirable, because it makes it difficult to capture the existence of
multiple reminders (or whatever other action) for a single assignment record.
It also becomes difficult to store additional data about errors if we model the data in this way.

Make use of the historical records on the assignment model
----------------------------------------------------------
This is closer to the chosen direction, but still makes it difficult to associate
particular errors/failures with particular actions taken for an assignment. It's also more difficult
to query compared to the chosen solution.
