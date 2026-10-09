"""Central configuration.

Every threshold, weight and limit used by the pipeline lives here so detectors never hard-code numbers.
Values can be overridden through environment variables or a `.env` file. Nested groups use a double
underscore, e.g. ``VISIBILITY__LOW_CONTRAST_RATIO=1.6`` or ``SCORING__WEIGHT_SKILLS=0.4``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class UploadSettings(BaseModel):
    max_file_mb: float = 5.0
    max_pages: int = 10
    max_files_per_request: int = 50
    zip_max_uncompressed_mb: float = 50.0  # DOCX zip-bomb guard: total uncompressed size
    zip_max_ratio: float = 120.0  # ...and overall compression ratio
    min_text_chars: int = 80  # fewer extractable characters than this → UNPARSEABLE (scanned/image-only)


class VisibilitySettings(BaseModel):
    render_dpi: int = 150
    low_contrast_ratio: float = 1.5  # WCAG contrast below this → LOW_CONTRAST
    tiny_font_hidden_pt: float = 4.0  # effective size below this → hidden
    tiny_font_suspicious_pt: float = 6.0  # 4–6 pt with enough characters → suspicious (medium)
    tiny_font_suspicious_min_chars: int = 20
    low_opacity: float = 0.1
    off_page_min_outside: float = 0.5  # share of the bbox outside the page box → OFF_PAGE
    covered_min_fraction: float = 0.9  # share of the bbox covered by a later opaque shape/image → COVERED
    cover_min_opacity: float = 0.9
    stacked_min_overlap: float = 0.6  # overlap (of the smaller bbox) for STACKED_TEXT
    stacked_same_text_ratio: float = (
        80.0  # rapidfuzz ratio above which stacked spans are the same text (faux bold)
    )
    pixel_check_min_chars: int = (
        3  # only spans with at least this many non-space characters get the pixel check
    )
    pixel_check_min_height_px: int = 5
    pixel_ink_delta: int = 48  # a pixel counts as "ink" when it differs this much from the background colour
    pixel_min_ink_fraction: float = 0.012  # less ink than this inside the bbox → not rendered visibly
    bg_ink_share: float = (
        0.3  # if the dominant colour equals the text colour, a 2nd colour this common is the real bg
    )
    ocr_layer_image_coverage: float = (
        0.6  # invisible text on a page mostly covered by an image = OCR layer (legit)
    )
    nonbody_min_words: int = 20  # comments / alt text / annotations are flagged when this long...
    nonbody_min_keywords: int = 2  # ...or when they contain this many JD keywords


class OcrSettings(BaseModel):
    enabled: bool = False
    lang: str = "eng"
    min_word_conf: int = 50


class DocxSettings(BaseModel):
    convert_to_pdf: bool = False  # LibreOffice headless; gives DOCX the same visual checks as PDF
    soffice_path: str = "soffice"
    default_font_pt: float = 10.0
    chars_per_page: int = 3200  # page-count estimate when docProps/app.xml has no <Pages>


class ScoringSettings(BaseModel):
    weight_requirements: float = 0.5
    weight_skills: float = 0.3
    weight_overall: float = 0.2
    calib_floor: float = 0.25  # raw cosine mapped to 0
    calib_ceiling: float = 0.75  # raw cosine mapped to 100
    overall_floor: float = 0.25
    overall_ceiling: float = 0.75
    requirement_met: float = 0.55
    skill_semantic_threshold: float = 0.80
    must_have_weight: float = 1.0
    nice_to_have_weight: float = 0.5
    gaming_delta_threshold: float = 10.0
    min_unit_words: int = 2


class IntegritySettings(BaseModel):
    jd_line_min_words: int = 8
    jd_verbatim_ratio: float = 90.0
    jd_partial_ratio: float = 95.0
    jd_partial_min_share: float = (
        0.6  # JD sentence must be ≥ this share of the resume unit for a partial match
    )
    jd_shingle_size: int = 8
    jd_bulk_high: float = 0.15
    jd_bulk_critical: float = 0.40
    jd_near_cosine: float = 0.92
    jd_near_overlap: float = 0.7
    stuffing_min_repeats: int = 8
    stuffing_token_ratio: float = 0.03
    dump_min_tokens: int = 15
    dump_keyword_ratio: float = 0.7
    skills_list_max_items: int = 50
    repeated_line_min: int = 3
    repeated_line_min_words: int = 4
    injection_semantic_threshold: float = 0.75
    injection_min_words: int = 4
    invisible_unicode_threshold: int = 5
    invisible_unicode_high: int = 50
    homoglyph_min_words: int = 1
    garbled_min_chars: int = 200
    garbled_ratio: float = 0.4
    metadata_max_words: int = 40
    metadata_max_items: int = 15
    metadata_min_keywords: int = 5
    near_duplicate_cosine: float = 0.97
    duplicate_scope: Literal["job", "global"] = "job"
    hidden_escalate_min_keywords: int = 3
    timeline_future_grace_days: int = 31


class RiskSettings(BaseModel):
    critical: int = 40
    high: int = 25
    medium: int = 10
    low: int = 3
    cap: int = 100


class DecisionSettings(BaseModel):
    shortlist_score: float = 70.0
    review_score: float = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(BACKEND_ROOT / ".env")),
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Resume Screening Engine"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://screener:screener@localhost:5433/screener"
    storage_dir: Path = REPO_ROOT / "var"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    max_concurrent_jobs: int = 2
    preview_dpi: int = 110
    load_models_on_startup: bool = True

    # NLP models
    sbert_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    spacy_model: str = "en_core_web_sm"
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    sbert_input: Literal["natural", "stopword_removed"] = "natural"
    embed_batch_size: int = 64
    embed_cache_size: int = 8192

    upload: UploadSettings = Field(default_factory=UploadSettings)
    visibility: VisibilitySettings = Field(default_factory=VisibilitySettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    docx: DocxSettings = Field(default_factory=DocxSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    integrity: IntegritySettings = Field(default_factory=IntegritySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def quarantine_dir(self) -> Path:
        return self.storage_dir / "quarantine"

    @property
    def previews_dir(self) -> Path:
        return self.storage_dir / "previews"


@lru_cache
def get_settings() -> Settings:
    return Settings()
