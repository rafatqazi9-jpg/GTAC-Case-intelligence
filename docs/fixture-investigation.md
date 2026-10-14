\# Fixture-Based Case Investigation



\## 1. Overview



The GTAC Case Intelligence application provides two separate analysis workflows:



1\. \*\*Pasted-text analysis:\*\* Analyzes customer-provided case descriptions, CLI output, and logs using the existing extraction workflow.

2\. \*\*Fixture-based investigation:\*\* Runs the case intelligence brief builder against locally available synthetic fixture data.



The fixture-based workflow is intended to demonstrate evidence-driven investigation, source provenance, confidence assessment, and abstention behavior.



\## 2. How to use the fixture workflow



1\. Start the local application using `python -m src.server`.

2\. Open the application in a browser at the address shown by the server.

3\. Locate the \*\*Fixture-Based Case Investigation\*\* panel.

4\. Enter a case ID that exists in the local mock-data fixtures.

5\. Select \*\*Investigate fixture\*\*.

6\. Review the generated brief, investigation status, findings, confidence levels, and source IDs.



An unknown case ID should return an error rather than silently substituting another case.



\## 3. API



\### Endpoint



`POST /api/investigate`



\### Request



```json

{

&#x20; "case\_id": "CS-6100050"

}

```



The case ID above is an example used by the automated tests. Other IDs must exist in the local fixture data.



\### Response



The response includes the case ID, generation timestamp, abstention status and reason, rendered brief, and structured findings.



Each finding can contain:



\- Module identifier

\- Finding type

\- Statement

\- Confidence

\- Source identifiers



\### Error handling



\- `400`: Invalid or missing case ID.

\- `404`: Case ID not found in fixture data.

\- `500`: Unexpected investigation error.



\## 4. Evidence-window behavior



The endpoint derives its evidence cutoff from the fixture case's creation timestamp plus two days. It passes that window to the existing brief builder with a medium confidence threshold.



This is a fixed demonstration rule. It should not be interpreted as a universal GTAC investigation policy.



\## 5. Limitations and safety boundaries



\- \*\*Synthetic data:\*\* The workflow uses the local mock adapter and fixture data.

\- \*\*No live Salesforce integration:\*\* Entering a case ID does not retrieve a live customer case.

\- \*\*No live device access:\*\* The workflow does not connect to network devices or collect new diagnostics.

\- \*\*Read-only behavior:\*\* The workflow builds a brief and does not modify a system of record.

\- \*\*Evidence-based output:\*\* Findings and their source identifiers should be reviewed before using conclusions in a customer-facing RCA.

\- \*\*Abstention:\*\* An abstention is a valid investigation outcome when the evidence does not support a sufficiently confident conclusion.

\- \*\*Test coverage:\*\* Automated tests cover successful investigation, unknown IDs, and invalid case IDs. Passing tests does not establish that the investigation logic is correct for every possible case.



\## 6. Verification



Run the complete test suite:



`python -m pytest -q`



Run the whitespace check:



`git diff --check`



The UI should also be tested manually for both workflows, including an unknown fixture ID.



\## 7. Future development



A useful next improvement is a fixture catalog that lists available synthetic cases and allows the user to select a case without manually knowing its ID.



This should remain separate from any future live Salesforce integration. A live integration would require its own authentication, authorization, data-handling, and testing design.

