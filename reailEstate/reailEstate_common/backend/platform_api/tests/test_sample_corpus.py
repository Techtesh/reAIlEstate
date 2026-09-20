from pathlib import Path

from platform_api.sample_corpus import SampleDocument, categorize_samples, prioritize_project_sources


def test_categorize_samples_excludes_ignore_directory_and_detects_duplicates(tmp_path):
    samples = tmp_path / "Samples"
    thane = samples / "Thane"
    ignored = samples / ".ignore"
    thane.mkdir(parents=True)
    ignored.mkdir()
    brochure = thane / "Crest Avenue Brochure.pdf"
    brochure.write_bytes(b"%PDF- sample brochure")
    duplicate = thane / "Crest Avenue Brochure (1).pdf"
    duplicate.write_bytes(b"%PDF- sample brochure")
    floor_plan = thane / "Crest Avenue Floor Plan.pdf"
    floor_plan.write_bytes(b"%PDF- floor plan")
    oversized = thane / "Crest Avenue Elevation.pdf"
    oversized.write_bytes(b"%PDF- elevation exceeds limit")
    ignored_file = ignored / "private.pdf"
    ignored_file.write_bytes(b"%PDF- ignored")

    documents, ignored_files = categorize_samples(samples, max_upload_bytes=20)

    assert [document.category for document in documents] == ["brochure", "brochure", "elevation", "floor_plan"]
    assert documents[1].duplicate_of == brochure
    assert not documents[2].eligible
    assert documents[3].project_name == "Crest Avenue"
    assert ignored_files == [ignored_file]


def test_categorize_samples_uses_locality_and_stable_project_key(tmp_path):
    pdf = tmp_path / "Vikhroli" / "AdityaRaj_Tapovan_Brochure.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF- project")

    documents, ignored_files = categorize_samples(Path(tmp_path), max_upload_bytes=1024)

    assert ignored_files == []
    assert len(documents) == 1
    assert documents[0].locality == "Vikhroli"
    assert documents[0].project_key == "vikhroli-adityaraj-tapovan"
    assert documents[0].category == "brochure"


def test_sample_document_supports_missing_maha_rera_as_null_placeholder(tmp_path):
    document = SampleDocument(
        path=tmp_path / "sample.pdf",
        locality="Thane",
        project_key="thane-sample",
        project_name="Sample",
        category="project_material",
        byte_length=10,
        sha256="0" * 64,
        eligible=True,
        maha_rera_ids=(),
    )

    assert document.maha_rera_ids == ()


def test_nested_project_sources_share_identity_and_support_documents(tmp_path):
    folder = tmp_path / "Thane" / "Crest-Avenue"
    nested = folder / "Tower B"
    nested.mkdir(parents=True)
    (folder / "brochure.pdf").write_bytes(b"%PDF- brochure")
    (folder / "extra.txt").write_text("Possession: December 2027")
    (nested / "details.docx").write_bytes(b"docx sample")
    (folder / "video.mp4").write_bytes(b"video")
    documents, _ = categorize_samples(tmp_path, max_upload_bytes=1024)
    assert len(documents) == 3
    assert {item.project_name for item in documents} == {"Crest-Avenue"}
    assert {item.project_key for item in documents} == {"thane/crest-avenue"}
    assert {item.media_type for item in documents} == {
        "application/pdf", "text/plain", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def test_project_sources_prioritize_pre_cut_images(tmp_path):
    folder = tmp_path / "Thane" / "Ashar Merac"
    folder.mkdir(parents=True)
    (folder / "brochure.pdf").write_bytes(b"%PDF- brochure")
    (folder / "Merac_444.png").write_bytes(b"pre-cut image")

    documents, _ = categorize_samples(tmp_path, max_upload_bytes=1024)
    prioritized = prioritize_project_sources(documents)

    assert {item.project_key for item in prioritized} == {"thane/ashar merac"}
    assert [item.source_role for item in prioritized] == ["precut_floor_plan_candidate", "supporting_evidence"]


def test_duplicate_content_in_different_projects_is_not_skipped(tmp_path):
    for name in ("One", "Two"):
        folder = tmp_path / "Thane" / name
        folder.mkdir(parents=True)
        (folder / "shared.txt").write_text("Shared supporting material")
    documents, _ = categorize_samples(tmp_path, max_upload_bytes=1024)
    assert len(documents) == 2
    assert all(item.duplicate_of is None for item in documents)


def test_skipped_sources_are_explicit(tmp_path):
    from platform_api.sample_corpus import skipped_samples
    ignored = tmp_path / ".ignore"
    ignored.mkdir()
    (ignored / "private.docx").write_bytes(b"ignored")
    (tmp_path / "video.MP4").write_bytes(b"video")
    (tmp_path / "link.gdoc").write_text("unsupported shortcut")
    assert {item["reason"] for item in skipped_samples(tmp_path)} == {
        "ignored_directory", "video", "unsupported_format"}


def test_aggregate_outputs_retains_conflicts_and_additional_evidence():
    from platform_api.sample_reports import aggregate_outputs
    outputs = [{"possession": {"value": date, "status": "extracted", "candidates": [
        {"value": date, "documentId": name, "evidenceText": date}]}}
        for name, date in [("brochure", "2027"), ("notes", "2028")]]
    result = aggregate_outputs(outputs)
    assert result["fields"]["possession"]["status"] == "conflict"
    assert result["fields"]["possession"]["value"] is None
    assert len(result["fields"]["possession"]["candidates"]) == 2
