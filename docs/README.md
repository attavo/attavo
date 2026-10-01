# docs/: what counsel reads

The method rules, briefs and rulings that govern this repository as its own tenant, at the version the manifest's `[counsel].reads` names. Counsel is handed these paths before it answers (`ATTAVO_COUNSEL_READS`); a path named in the manifest that does not exist is refused, not skipped. The living copies of the operator's documents are kept elsewhere; what is here is what a record may cite, and a citation into `docs/` resolves to a file in the repository at the record's commit.
