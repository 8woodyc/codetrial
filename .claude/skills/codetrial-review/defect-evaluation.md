# Measuring whether a review finds defects

Nothing else in this skill measures that. [evaluation.md](evaluation.md) scores
retrieval and [benchmarking.md](benchmarking.md) scores bootstrap cost. A
finding count, a confirmed verdict or a later code edit is not ground truth: the
reviewer produced the first two and the third has many causes. Report what an
experiment can support and no more.

## What a replay of a merged change can and cannot show

A replay reviews an earlier revision whose later fix is known. It is
contaminated whenever the material the reviewer reads was written after that
revision: a rule distilled from the fix's own thread, a history page that
contains the fix, an index built later. A hit on such material shows that a
known rule was applied, not that an unseen defect was found.

To replay cleanly, review in a checkout of the earlier revision with the rules
and contracts as they were then, and no history from after it. The `created`
lower bound of `memory.py search` limits what a query returns but cannot rebuild
the past, because a comment edited later keeps its old creation time with new
text. Where the earlier text is not available, leave the evidence out or label
the replay contaminated; never present it as a recall figure.

## The pilot

The smallest design that gives an estimate worth reading:

1. Freeze the skill, rules and prompts under test.
2. An independent person writes four small buggy diffs, each under the
   single-reviewer threshold, from mechanisms not already named in the rules,
   and a corrected control for each. Exclude defects already discussed with the
   reviewers.
3. Two arms see identical code, contracts, model, tools and limits: a plain
   review prompt, and this skill. Memory is off in both.
4. Each target is reviewed twice per arm in fresh contexts, with the variant
   hidden and the order randomized: 32 reviews.
5. A reviewer blind to the arm reproduces and classifies every distinct claim.
   An unexpected valid defect counts; a claim missing from the seeded list is
   not false for that reason.

Report seeded-defect recall, adjudicated precision, false findings per review
and per control, claims left unresolved, severity and cost, with the paired
outcome per target. Repetitions of a target measure variability, not extra
defects. Four targets give a narrow estimate with wide uncertainty and say
nothing about other kinds of defect.

## Recording what produced a finding

A case in an experience report may name its `rule`, `lens` and `reviewer`
([experience.md](experience.md)). With later outcomes recorded by `experience
resolve`, they allow a count of confirmed against `not-reproduced` per rule. A
recall figure still needs defects found some other way: findings recorded here
are never its denominator.
