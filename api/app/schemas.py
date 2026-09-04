# GENERATED FILE - DO NOT HAND-EDIT.
# Regenerate with: python scripts/gen_schemas.py
#
# Source: schemas/*.schema.json, the only cross-track contract (see CLAUDE.md).
# Changing a schema means regenerating this file and web/src/contracts/ in the
# same commit.
#
# One section per schema file, in docs/SPEC.md section 4 order.


from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, RootModel, confloat, conint, constr

# -----------------------------------------------------------------------------
# from schemas/planogram.schema.json
# -----------------------------------------------------------------------------

class Source(Enum):
    manual = 'manual'
    video = 'video'


class Type(Enum):
    shelf = 'shelf'
    endcap = 'endcap'


class Station(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    camera_pos: List[float] = Field(..., max_length=3, min_length=3)
    look_at: List[float] = Field(..., max_length=3, min_length=3)


class Level(Enum):
    top = 'top'
    above_eye = 'above_eye'
    eye = 'eye'
    below_eye = 'below_eye'
    bottom = 'bottom'


class Slot(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    slot_id: str
    sku_id: Optional[str] = None
    facings: conint(ge=0)
    x_m: confloat(ge=0.0)
    width_m: PositiveFloat
    height_m: PositiveFloat
    confidence: Optional[confloat(ge=0.0, le=1.0)] = None


class Type1(Enum):
    shelf_talker = 'shelf_talker'
    endcap_header = 'endcap_header'
    floor_decal = 'floor_decal'
    screen = 'screen'


class AdSlot(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ad_slot_id: str
    type: Type1
    attached_to: str
    x_m: confloat(ge=0.0)
    width_m: PositiveFloat
    creative_id: Optional[str] = None
    confidence: Optional[confloat(ge=0.0, le=1.0)] = None


class Sku(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    sku_id: str
    name: str
    brand: str
    category: str
    price: confloat(ge=0.0)
    promo: bool
    texture_url: str
    color_lab: List[float] = Field(..., max_length=3, min_length=3)


class Creative(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    creative_id: str
    brand: str
    texture_url: str


class Shelf(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    shelf_id: str
    height_m: confloat(ge=0.0)
    level: Level
    slots: List[Slot]


class Bay(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    bay_id: str
    type: Type
    width_m: PositiveFloat
    height_m: PositiveFloat
    station: Station
    shelves: List[Shelf] = Field(..., min_length=1)
    ad_slots: List[AdSlot]


class Planogram(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    planogram_id: str
    name: str
    source: Source
    bays: List[Bay] = Field(..., min_length=1)
    skus: List[Sku] = Field(..., min_length=1)
    creatives: List[Creative]


# -----------------------------------------------------------------------------
# from schemas/variant.schema.json
# -----------------------------------------------------------------------------

class Patch1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    op: Literal['move_sku']
    sku_id: str
    to_slot_id: str


class Patch2(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    op: Literal['set_ad_creative']
    ad_slot_id: str
    creative_id: Optional[str] = None


class Patch3(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    op: Literal['swap_texture']
    sku_id: str
    texture_url: str


class Patch4(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    op: Literal['set_price']
    sku_id: str
    price: confloat(ge=0.0)
    promo: Optional[bool] = None


class Patch(RootModel[Union[Patch1, Patch2, Patch3, Patch4]]):
    root: Union[Patch1, Patch2, Patch3, Patch4]


class Variant(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    variant_id: str
    base_planogram_id: str
    name: str
    patches: List[Patch]


# -----------------------------------------------------------------------------
# from schemas/session.schema.json
# -----------------------------------------------------------------------------

class Mode(Enum):
    webcam = 'webcam'
    cursor_only = 'cursor_only'


class Intake(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    has_list: bool
    same_brand: bool
    hurry: bool


class ArchetypeLabel(Enum):
    mission = 'mission'
    browser = 'browser'
    loyalist = 'loyalist'
    switcher = 'switcher'
    NoneType_None = None


class RejectReason(Enum):
    too_short = 'too_short'
    one_station = 'one_station'
    no_interaction = 'no_interaction'
    low_coverage = 'low_coverage'
    no_consent = 'no_consent'
    NoneType_None = None


class Quality(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    fixation_coverage: Optional[confloat(ge=0.0, le=1.0)] = None
    stations_visited: Optional[conint(ge=0)] = None
    duration_s: Optional[confloat(ge=0.0)] = None


class Session(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    session_id: str
    variant_id: str
    consent: bool
    started_at: datetime
    ended_at: Optional[datetime] = None
    screen_w: int
    screen_h: int
    mode: Mode
    calibration_error_px: Optional[float] = None
    intake: Optional[Intake] = None
    archetype_label: Optional[ArchetypeLabel] = None
    prediction_id: Optional[str] = None
    accepted: Optional[bool] = None
    reject_reason: Optional[RejectReason] = None
    quality: Optional[Quality] = None


# -----------------------------------------------------------------------------
# from schemas/event.schema.json
# -----------------------------------------------------------------------------

class EventType(Enum):
    gaze = 'gaze'
    fixation = 'fixation'
    cursor_dwell = 'cursor_dwell'
    hover = 'hover'
    pickup = 'pickup'
    add_to_cart = 'add_to_cart'
    remove = 'remove'
    station_enter = 'station_enter'
    station_exit = 'station_exit'
    checkout = 'checkout'


class Event(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    t_ms: conint(ge=0)
    type: EventType
    station_id: Optional[str] = None
    payload: Dict[str, Any]


# -----------------------------------------------------------------------------
# from schemas/simresult.schema.json
# -----------------------------------------------------------------------------

class Path(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    stations_mean: confloat(ge=0.0)
    duration_s_mean: confloat(ge=0.0)


class SimResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    sim_run_id: str
    variant_id: str
    persona_id: str
    n_runs: conint(ge=1)
    seed: int
    fixation_prob: Dict[str, confloat(ge=0.0, le=1.0)]
    dwell_ms_mean: Dict[str, confloat(ge=0.0)]
    ad_slot_attention: Dict[str, confloat(ge=0.0, le=1.0)]
    purchase_share: Dict[str, confloat(ge=0.0, le=1.0)]
    ad_exposed_purchase_share: Optional[Dict[str, float]] = None
    ad_unexposed_purchase_share: Optional[Dict[str, float]] = None
    path: Path
    traces: Optional[List[str]] = None


# -----------------------------------------------------------------------------
# from schemas/metrics.schema.json
# -----------------------------------------------------------------------------

class PerVariant(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    attention_spearman: float
    heatmap_kl: float
    purchase_share_mae: float
    ad_slot_index_spearman: Optional[float] = None


class DecisionAgreement(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kpi: str
    winner_real: str
    winner_synth: str
    agree: bool


class NoiseCeiling(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    spearman_mean: float
    ci95: List[float] = Field(..., max_length=2, min_length=2)
    n_splits: int


class KnownEffect(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    real_uplift: Optional[float] = None
    synth_uplift: Optional[float] = None
    same_direction: Optional[bool] = None


class AdToPurchaseLift(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    real: Optional[float] = None
    synth: Optional[float] = None
    ci95: Optional[List[float]] = Field(None, max_length=2, min_length=2)


class ExperimentMetrics(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    experiment_id: str
    fit_variant: str
    holdout_variants: List[str]
    per_variant: Dict[str, PerVariant]
    decision_agreement: DecisionAgreement
    noise_ceiling: NoiseCeiling
    relative_agreement: Optional[float] = None
    known_effect: Optional[KnownEffect] = None
    ad_to_purchase_lift: Optional[Dict[str, AdToPurchaseLift]] = None
    segment_fidelity: Optional[Dict[str, float]] = None
    n_real_accepted: int
    n_real_rejected: int
    n_synth: int
    calibrated_shares: Optional[Dict[str, float]] = None


# -----------------------------------------------------------------------------
# from schemas/persona.schema.json
# -----------------------------------------------------------------------------

class Archetype(Enum):
    mission = 'mission'
    browser = 'browser'
    loyalist = 'loyalist'
    switcher = 'switcher'


class Persona(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    persona_id: str
    archetype: Archetype
    description: str
    share_of_population: confloat(ge=0.0, le=1.0)


# -----------------------------------------------------------------------------
# from schemas/policy.schema.json
# -----------------------------------------------------------------------------

class TimeBudgetS(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    mean: PositiveFloat
    sd: confloat(ge=0.0)


class DwellMs(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    mu: float
    sigma: PositiveFloat


class FixationsPerStation(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    lam: PositiveFloat


class PersonaPolicy(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    persona_id: str
    goal_categories: List[str]
    time_budget_s: TimeBudgetS
    exploration: confloat(ge=0.0, le=1.0)
    brand_affinity: Dict[str, confloat(ge=0.0, le=1.0)]
    price_sensitivity: confloat(ge=0.0, le=1.0)
    promo_sensitivity: confloat(ge=0.0, le=1.0)
    ad_receptivity: confloat(ge=0.0, le=1.0)
    purchase_threshold: confloat(ge=0.0, le=1.0)
    dwell_ms: DwellMs
    fixations_per_station: FixationsPerStation


# -----------------------------------------------------------------------------
# from schemas/prediction.schema.json
# -----------------------------------------------------------------------------

class PredictionLock(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    prediction_id: str
    session_id: str
    variant_id: str
    sim_run_id: str
    created_at: datetime
    population_fixation_prob: Dict[str, float]
    sha256: constr(pattern=r'^[a-f0-9]{64}$')
    git_commit: Optional[str] = None
