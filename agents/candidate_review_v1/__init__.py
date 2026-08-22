"""Candidate Review v1 additive product package."""

from agents.candidate_review_v1.candidate_engine import (  # noqa: F401
    CANDIDATE_ANCHOR_KINDS,
    CandidateBuildResult,
    CandidateRankSignals,
    CandidateSeed,
    EvidenceAlias,
    RankedCandidate,
    build_candidate_inventory,
)

from agents.candidate_review_v1.calendar_engine import (  # noqa: F401
    MAX_VISIBLE_RESEARCHED_EVENTS,
    CalendarBuildResult,
    CalendarCandidateDateOwner,
    CalendarVehicleDateOwner,
    CalendarCoverageReceipt,
    CalendarDisposition,
    CalendarEvidenceAttribution,
    CalendarItem,
    CalendarItemOrigin,
    DeferredCalendarItem,
    EventSeed,
    add_calendar_months,
    build_calendar,
    project_document_calendar,
)

from agents.candidate_review_v1.collaboration import (  # noqa: F401
    CollaborationComparison,
    CollaborationLayer,
    CollaborationMode,
    CollaborationRequest,
    CollaborationRun,
    CollaboratorProvider,
    CollaboratorState,
    InferenceChallengeOutput,
    IntakeChallengeOutput,
    ProviderCallResult,
    RunDisposition,
    SanitizedEvidence,
    SanitizedIntake,
    TokenUsage,
    compare_collaboration,
    sanitize_evidence_snapshot,
    sanitize_intake,
)

from agents.candidate_review_v1.collaboration_providers import (  # noqa: F401
    ClaudeMaxAdapter,
    OpenAIResponsesAdapter,
)

from agents.candidate_review_v1.collaboration_runner import (  # noqa: F401
    CollaborationExecutionReceipt,
    CollaborationExecutionResult,
    CollaborationRunner,
    ProviderExecutionSource,
)

from agents.candidate_review_v1.collaboration_session import (  # noqa: F401
    CollaborationSessionStatus,
    OpenAICollaborationSession,
    clear_openai_collaboration,
    configure_openai_collaboration,
    openai_api_key_for_provider_call,
    openai_collaboration_status,
)

from agents.candidate_review_v1.collaboration_store import (  # noqa: F401
    CollaborationCacheIdentity,
    CollaborationStore,
    CollaborationStoreReceipt,
)

from agents.candidate_review_v1.contracts import (  # noqa: F401
    CONTENT_BUDGETS,
    CURRENT_WATCH_MAX_AGE,
    CURRENT_CANDIDATE_NOTICE_ROLES,
    FORBIDDEN_CONTROL_TOKENS,
    REQUIRED_CONTEXTUAL_EDITOR_CONTROLS,
    REQUIRED_DOCUMENT_CONTROLS,
    REQUIRED_PRODUCT_SURFACES,
    REQUIRED_WORKFLOW_ACTIONS,
    NEAR_TERM_WATCH_DAYS,
    NEAR_TERM_WATCH_MAX_AGE,
    SCHEMA_VERSION,
    SECTION_ORDER,
    ArtifactBinding,
    CandidateReviewDocument,
    CandidateUnit,
    CoverageRecord,
    DateKind,
    DateValue,
    EditorState,
    EventLifecycleStatus,
    EventRecord,
    EvidenceRecord,
    MoneyValue,
    NoticeRole,
    VehicleAccessPosture,
    VehicleActivityIdentity,
    VehicleClass,
    VehicleIdentity,
    VehicleOnRampStatus,
    VehicleOrderingStatus,
    VehicleParticipant,
    VehicleParticipantRole,
    VehiclePartnerRoute,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
    VehicleWatchRecord,
    validate_event_watch_freshness,
    validate_vehicle_signal_evidence,
    validate_vehicle_watch_record_evidence,
)

from agents.candidate_review_v1.event_research import (  # noqa: F401
    MANIFEST_VERSION,
    EventDiscoveryResult,
    EventLead,
    EventQueryAttempt,
    EventQueryFamily,
    EventQueryManifest,
    EventQuerySpec,
    EventResearchFrame,
    EventSearchResponse,
    EventSourceClass,
    build_event_query_manifest,
    normalize_trusted_organizer_domain,
    run_event_discovery,
)

from agents.candidate_review_v1.event_watch import (  # noqa: F401
    DEFAULT_EVENT_WATCH_UNIVERSE_PATH,
    EVENT_WATCH_SCHEMA_VERSION,
    EventWatchUniverse,
    WatchCadence,
    WatchEventSeries,
    WatchFrameTags,
    WatchIdentityStatus,
    WatchOrganizer,
    WatchPriority,
    WatchSourceRole,
    WatchTarget,
    WatchTargetKind,
    WatchTargetSelection,
    load_event_watch_universe,
    select_watch_targets,
    watch_universe_sha256,
)

from agents.candidate_review_v1.persistence import (  # noqa: F401
    CURRENT_POINTER_FILENAME,
    CURRENT_POINTER_SCHEMA_VERSION,
    GENERATION_RECEIPT_FILENAME,
    GENERATION_RECEIPT_SCHEMA_VERSION,
    CurrentGenerationPointer,
    GenerationCommit,
    GenerationFileBinding,
    GenerationFileRole,
    GenerationPersistenceError,
    GenerationReceipt,
    LoadedGeneration,
    default_generation_state_root,
    find_reusable_generation,
    generation_basis_sha256,
    load_current_generation,
    persist_generation,
)

from agents.candidate_review_v1.pipeline import (  # noqa: F401
    PIPELINE_SCHEMA_VERSION,
    CandidateReviewGeneration,
    CandidateReviewGenerationPlan,
    EventWatchCheck,
    EventWatchCheckProjection,
    ProviderBinding,
    ProviderMode,
    ResearchProviderBindings,
    ResearchSnapshotChange,
    ResearchSnapshotDiff,
    SnapshotChangeKind,
    SnapshotDiffState,
    SnapshotLane,
    build_research_snapshot_diff,
    execute_candidate_review_generation,
    generate_candidate_review_generation,
    plan_candidate_review_generation,
    project_next_event_watch_checks,
)

from agents.candidate_review_v1.replay_providers import (  # noqa: F401
    EVENT_REPLAY_ARTIFACT_VERSION,
    EVENT_REPLAY_SOURCE_VERSION,
    REPLAY_ADAPTER_VERSION,
    REPLAY_PROVIDER_CONFIG_VERSION,
    REPLAY_SOURCE_BUNDLE_VERSION,
    CurrentSweepReplay,
    CurrentSweepSource,
    EventReplayArtifact,
    EventReplayProvider,
    EventReplaySourceArtifact,
    ReplayFileReference,
    ReplayProviderConfig,
    ReplayProviderError,
    ReplayProviderRuntime,
    ReplaySourceBundleConfig,
    VehicleSweepReplayProvider,
    load_replay_provider_runtime,
    load_replay_source_bundle_runtime,
)

from agents.candidate_review_v1.vehicle_watch import (  # noqa: F401
    PUBLIC_VEHICLE_WATCH_LANES,
    VEHICLE_WATCH_SCHEMA_VERSION,
    RestrictedVehicleExport,
    VehicleCollectionResult,
    VehicleLead,
    VehicleQueryAttempt,
    VehicleQueryManifest,
    VehicleQuerySpec,
    VehicleSearchResponse,
    VehicleTargetKind,
    VehicleWatchFrame,
    VehicleWatchLane,
    VehicleWatchSearcher,
    build_vehicle_query_manifest,
    project_vehicle_watch_coverage,
    run_vehicle_watch_collection,
)

from agents.candidate_review_v1.watch_diff import (  # noqa: F401
    WATCH_DIFF_SCHEMA_VERSION,
    VerifiedWatchChange,
    VerifiedWatchDiff,
    WatchChangeKind,
    append_verified_watch_ticker,
    diff_verified_watch,
)

from agents.candidate_review_v1.composer import (  # noqa: F401
    COMPOSER_SCHEMA_VERSION,
    CandidateReviewComposition,
    ComposerDrop,
    ComposerError,
    compose_candidate_review_document,
)

from agents.candidate_review_v1.renderer import (  # noqa: F401
    RENDERER_VERSION,
    render_candidate_review,
)

from agents.candidate_review_v1.verification import (  # noqa: F401
    VERIFICATION_SCHEMA_VERSION,
    AnchorOffer,
    FetchedPage,
    FetchedRecord,
    VerificationDrop,
    VerificationResult,
    verify_research,
)

from agents.candidate_review_v1.authoring import (  # noqa: F401
    AUTHORING_SCHEMA_VERSION,
    AuthoringDrop,
    AuthoringResult,
    author_candidate_review,
)

from agents.candidate_review_v1.document_release import (  # noqa: F401
    CandidateReviewCertificate,
    ReleaseError,
    certify_candidate_review_release,
    write_candidate_review_release,
)
