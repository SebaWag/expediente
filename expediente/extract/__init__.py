"""The extraction layer: a document becomes fields, never findings.

The extractor is allowed to be wrong about a value, and allowed to be missing a
value. It is not allowed to be silent about either. Every requested field leaves
this layer in exactly one of two shapes: a value with the page it came from, or a
name in ``unverified`` with a cause code.

That rule exists because of the failure mode that actually kills an evidence
engine. Not the wrong number — a wrong number is caught by a human reading the
citation. The empty answer is what kills it: a model that burns its output budget
on reasoning tokens and returns nothing looks, downstream, exactly like a document
that had nothing to say. Measured on a public benchmark run during this project's
workshop, 19 of 400 answers at maximum reasoning effort came back empty for that
reason alone (4.75%), with no error raised. An engine that cannot tell "absent"
from "not looked at" will report a clean dossier over a document it never read.

So the contract is narrow and boring on purpose: fill cells in a table that
deterministic code already knows how to read. Decide nothing.
"""
