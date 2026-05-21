from services.patent.bigquery_patent_service import (
    fetch_foreign_claims_from_bigquery,
    publication_number_candidates,
)


class QueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append({"sql": sql, "job_config": job_config})
        return QueryJob(self.rows)


def test_publication_number_candidates_uses_original_number_first():
    candidate = {
        "country_code": "JP",
        "document_number": "29047511",
        "kind_code": "A",
        "original_number": "JP2017047511 A",
        "display_number": "JP29047511 A",
    }

    assert publication_number_candidates(candidate) == [
        "JP-2017047511-A",
        "JP-29047511-A",
        "JP-29047511",
    ]


def test_fetch_foreign_claims_from_bigquery_maps_claim_rows():
    client = Client(
        [
            {
                "publication_number": "CN-113039310-A",
                "country_code": "CN",
                "kind_code": "A",
                "claim_language": "en",
                "claim_text": "A first foreign claim.",
            },
            {
                "publication_number": "CN-113039310-A",
                "country_code": "CN",
                "kind_code": "A",
                "claim_language": "en",
                "claim_text": "A second foreign claim.",
            },
        ]
    )
    candidates = [
        {
            "direction": "cited_by_target",
            "country_code": "CN",
            "document_number": "113039310",
            "kind_code": "A",
            "display_number": "CN113039310 A",
        }
    ]

    result = fetch_foreign_claims_from_bigquery(candidates, client=client)

    assert "`patents-public-data.patents.publications`" in client.calls[0]["sql"]
    assert result == [
        {
            "direction": "cited_by_target",
            "country_code": "CN",
            "publication_number": "CN-113039310-A",
            "document_number": "113039310",
            "kind_code": "A",
            "display_number": "CN113039310 A",
            "representative_claims": [
                {
                    "claim_no": 1,
                    "text": "A first foreign claim.",
                    "language": "en",
                    "is_independent": True,
                    "dependency": None,
                },
                {
                    "claim_no": 2,
                    "text": "A second foreign claim.",
                    "language": "en",
                    "is_independent": False,
                    "dependency": None,
                },
            ],
            "lookup_status": "resolved",
            "lookup_source": "bigquery_patents_publications",
            "source_document": candidates[0],
        }
    ]


def test_fetch_foreign_claims_from_bigquery_omits_documents_without_claims():
    client = Client([])
    candidates = [
        {
            "direction": "cited_by_target",
            "country_code": "CN",
            "document_number": "113039310",
            "kind_code": "A",
            "display_number": "CN113039310 A",
        }
    ]

    result = fetch_foreign_claims_from_bigquery(candidates, client=client)

    assert result == []
