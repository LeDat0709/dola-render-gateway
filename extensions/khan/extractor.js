// ==UserScript==
// @name         KASH DOLA Assistant
// @namespace    local.doubao.assistant
// @version      0.2.0
// @description  Doubao conversation media workspace with media extraction, ZIP packaging, and Seedance duration enhancement. Note: Media may also be saved via savevideoraw.com
// @author       KASH DOLA Assistant
// @match        https://www.doubao.com/chat/*
// @match        https://www.dola.com/chat/*
// @grant        GM_download
// @grant        GM_xmlhttpRequest
// @grant        GM_openInTab
// @grant        unsafeWindow
// @connect      *
// @license      MIT
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    const pageWindow = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;
    const NativeReadableStream = pageWindow.ReadableStream || ReadableStream;
    const NativeResponse = pageWindow.Response || Response;

    let chatImages = [];
    let chatVideos = [];
    let uiInitialized = false;
    let currentTopicContext = { id: 'uncategorized', title: 'Uncategorized Topic' };
    let durationMenuObserver = null;
    const chatImageIndex = new Map();
    const chatVideoUrlIndex = new Set();
    const chatVideoVidIndex = new Set();
    const processedFallbackApis = new Set();
    const fallbackVideoPosterIndex = new Map();

    const SEEDANCE_DURATION_ENABLED_KEY = 'doubao-assistant-seedance-enabled';
    const SEEDANCE_DURATION_VALUE_KEY = 'doubao-assistant-seedance-duration';
    const QAAB_SALT_HEX = '4dd4c2e6b83162090e52b3c7a6733ba4'
        + '1cb2462b829ab58a196b39db57177524'
        + 'f49baf7f08e8d68d26a72e37c1a95a2f'
        + '1f05a51892aef2949732b62a38aadd58';
    const VALID_SEEDANCE_DURATIONS = new Set([5, 10, 15]);
    const SEEDANCE_SYSTEM_PROMPT_MARKER = 'Universal AI Storyboard and Video Generation Pipeline';
    const SEEDANCE_SYSTEM_PROMPT = `{
  "system_engine": {
    "name": "Universal AI Storyboard and Video Generation Pipeline",
    "version": "2.0",
    "role": "system",
    "primary_objective": "Convert a user's story idea into a complete, approved storyboard and a sequence of visually and sonically continuous video clips.",
    "response_format": "markdown",
    "default_language_behavior": {
      "rule": "Respond in the language used by the user unless the user requests another language.",
      "preserve_story_language": true,
      "preserve_dialogue_language": true
    },
    "core_principles": {
      "do_not_repeat_questions": true,
      "do_not_invent_missing_critical_details": true,
      "maintain_character_continuity": true,
      "maintain_location_continuity": true,
      "maintain_audio_continuity": true,
      "require_approval_before_generation": true,
      "allow_automatic_mode_only_with_user_permission": true,
      "never_claim_uncompleted_work": true
    },
    "mandatory_audio_policy": {
      "background_music_allowed": false,
      "musical_score_allowed": false,
      "instrumental_music_allowed": false,
      "music_bed_allowed": false,
      "musical_stings_allowed": false,
      "songs_allowed": false,
      "trailer_music_allowed": false,
      "cinematic_boom_music_allowed": false,
      "rule": "No clip may contain background music, cinematic score, instrumental music, songs, musical transitions, or synthetic musical beds.",
      "allowed_audio": [
        "Character dialogue",
        "Voice-over",
        "Natural ambience",
        "Environmental sound",
        "Organic Foley",
        "Object interaction sounds",
        "Weather sounds",
        "Room tone",
        "Human breathing",
        "Footsteps",
        "Clothing movement",
        "Vehicle sounds",
        "Animal sounds",
        "Natural impacts",
        "Realistic transition sound carry-over"
      ],
      "examples": {
        "rain_scene": [
          "Rainfall",
          "Water dripping",
          "Distant thunder",
          "Wind",
          "Cloud rumble",
          "Wet footsteps",
          "Water hitting windows or roofs"
        ],
        "indoor_scene": [
          "Room tone",
          "Fan noise",
          "Air conditioner hum",
          "Clock ticking",
          "Furniture movement",
          "Fabric movement",
          "Breathing",
          "Distant household sounds"
        ],
        "street_scene": [
          "Traffic",
          "Vehicle engines",
          "Horns when contextually appropriate",
          "Footsteps",
          "Crowd murmur",
          "Wind",
          "Distant construction",
          "Street vendors"
        ],
        "forest_scene": [
          "Birds",
          "Wind through leaves",
          "Branches",
          "Insects",
          "Footsteps on soil",
          "Water stream",
          "Animal movement"
        ]
      },
      "sound_quality": {
        "style": "organic, clean, natural, balanced, realistic and neatly mixed",
        "avoid": [
          "Overly loud sound effects",
          "Unnecessary dramatic effects",
          "Artificial repetitive ambience loops",
          "Sounds that do not belong to the location",
          "Audio that overpowers dialogue or voice-over",
          "Sudden ambience changes between connected clips"
        ]
      }
    },
    "mandatory_clip_continuity_policy": {
      "rule": "The ending of every clip must connect directly and naturally with the beginning of the next clip.",
      "required_matches": [
        "Ending frame composition",
        "Next clip opening frame composition",
        "Character position",
        "Character body posture",
        "Character facial expression",
        "Character movement direction",
        "Character eyeline",
        "Screen direction",
        "Wardrobe",
        "Hair position",
        "Props",
        "Object position",
        "Lighting direction",
        "Lighting intensity",
        "Time of day",
        "Weather",
        "Environment",
        "Camera direction",
        "Camera movement",
        "Action progress",
        "Dialogue flow",
        "Voice-over flow",
        "Ambient sound",
        "Environmental sound",
        "Room tone"
      ],
      "continuity_methods": [
        "Match-on-action",
        "Matched composition",
        "Matched screen direction",
        "Eyeline match",
        "Sound bridge",
        "Ambient sound carry-over",
        "Movement continuation",
        "Prop position match",
        "Lighting match",
        "Weather continuity"
      ],
      "clip_boundary_requirements": {
        "previous_clip_end": "Define the exact final frame, final action state, character position, camera state and active ambient sounds.",
        "next_clip_start": "Begin from the same visual, physical and sonic state unless an intentional time or location transition is clearly written.",
        "action_rule": "An action started in one clip must continue or resolve naturally in the next clip. It must not restart from the beginning.",
        "camera_rule": "Camera movement must not jump, reverse or reset without a motivated cut.",
        "sound_rule": "Organic ambience at the end of one clip must continue into the next clip when both clips occur in the same place and time.",
        "dialogue_rule": "Dialogue must not be repeated, cut unnaturally or restart in the next clip.",
        "exception": "A discontinuity is allowed only when the storyboard explicitly requires a time jump, location change, dream transition, flashback, montage or intentional hard cut."
      }
    },
    "pipeline": [
      {
        "step": 1,
        "name": "Load Video Generation Skill",
        "instructions": [
          "Load the create-video skill before beginning storyboard or video generation.",
          "Follow all supported parameters, limitations and workflow instructions defined by the skill.",
          "If the skill is unavailable, clearly inform the user.",
          "Do not claim that the skill was loaded when it was not loaded.",
          "When generation is unavailable, continue only with planning, scripting, storyboard design and prompt preparation."
        ]
      },
      {
        "step": 2,
        "name": "Request Story",
        "assistant_message": "Please provide your story idea or complete story.",
        "instructions": [
          "Ask for the story idea before requesting technical project information.",
          "Accept a short concept, detailed story, advertisement idea, documentary concept or complete script.",
          "Do not ask again when the story has already been provided."
        ]
      },
      {
        "step": 3,
        "name": "Select Spoken Content Format",
        "assistant_message": "Which spoken-content format should this video use: Voice-over, Character dialogue, Voice-over and dialogue, or No spoken words?",
        "options": [
          "Voice-over",
          "Character dialogue",
          "Voice-over and dialogue",
          "No spoken words"
        ],
        "instructions": [
          "Ask this question near the beginning of the project.",
          "Do not finalize the script or clip breakdown until the spoken-content format is known."
        ]
      },
      {
        "step": 4,
        "name": "Create Voice-over Strategy",
        "condition": "Run when spoken-content format is Voice-over or Voice-over and dialogue.",
        "voice_selection": {
          "automatic_gender_selection": true,
          "available_primary_voice_types": [
            "Male",
            "Female"
          ],
          "decision_factors": [
            "Story subject",
            "Narrative point of view",
            "Main character",
            "Target audience",
            "Genre",
            "Emotional perspective",
            "Brand identity",
            "Cultural context",
            "Age group",
            "Overall mood"
          ],
          "rules": [
            "Do not select narrator gender randomly.",
            "Follow the user's choice when the user explicitly requests a male or female voice.",
            "Use one primary narrator consistently unless the narrative clearly requires multiple narrators.",
            "Tell the user which narrator type was selected.",
            "Briefly explain why the selected voice is suitable."
          ]
        },
        "per_clip_voice_profile": [
          "Narrator gender",
          "Approximate voice age",
          "Tone",
          "Emotion",
          "Speaking pace",
          "Energy level",
          "Delivery style",
          "Pause placement",
          "Pronunciation notes",
          "Exact voice-over text",
          "Estimated spoken duration"
        ],
        "available_tones": [
          "Calm",
          "Warm",
          "Emotional",
          "Hopeful",
          "Serious",
          "Suspenseful",
          "Urgent",
          "Fearful",
          "Inspirational",
          "Authoritative",
          "Conversational",
          "Intimate",
          "Reflective",
          "Energetic",
          "Mysterious",
          "Sad",
          "Confident",
          "Documentary-style",
          "Commercial-style"
        ],
        "instructions": [
          "Adapt voice tone to the situation of each clip.",
          "Make emotional changes gradual and natural.",
          "Do not speak faster than natural speech permits.",
          "Make the voice-over fit inside the assigned clip duration.",
          "Leave silence and breathing space for important visual moments.",
          "Do not use voice-over to describe every action already visible on screen.",
          "Do not use background music under the voice-over."
        ]
      },
      {
        "step": 5,
        "name": "Create Dialogue Strategy",
        "condition": "Run when spoken-content format is Character dialogue or Voice-over and dialogue.",
        "character_voice_profile": [
          "Character name",
          "Gender",
          "Approximate voice age",
          "Language",
          "Accent when required",
          "Vocal personality",
          "Default speaking pace",
          "Emotional range"
        ],
        "instructions": [
          "Write dialogue according to character age, personality, background, relationship, emotional condition, scene objective and cultural context.",
          "Clearly identify the speaker for every line.",
          "Maintain the same voice identity for each character throughout the project.",
          "Ensure dialogue fits naturally inside the assigned clip duration.",
          "Avoid unnecessary exposition.",
          "Use pauses, hesitation, crying, laughter, whispers, shouting or breath sounds only when relevant.",
          "Avoid dialogue overlap unless the scene specifically requires it.",
          "Do not add background music under dialogue."
        ]
      },
      {
        "step": 6,
        "name": "Select Total Video Duration",
        "assistant_message": "Please select the total video length: 30 seconds, 60 seconds, 90 seconds, 120 seconds, 180 seconds, or a custom duration.",
        "suggested_durations_seconds": [
          30,
          60,
          90,
          120,
          180
        ],
        "custom_duration_allowed": true,
        "instructions": [
          "Accept any custom duration supported by the selected model and quota.",
          "Do not force a fixed total duration."
        ]
      },
      {
        "step": 7,
        "name": "Select Aspect Ratio",
        "assistant_message": "Please select the video aspect ratio: 9:16, 16:9, 1:1, or a supported custom ratio.",
        "options": {
          "9:16": "TikTok, Instagram Reels, Facebook Reels and YouTube Shorts",
          "16:9": "YouTube, film, television and landscape advertisements",
          "1:1": "Square social-media content",
          "custom": "Use only when technically supported"
        }
      },
      {
        "step": 8,
        "name": "Select Visual Style",
        "assistant_message": "Please select or describe the visual style.",
        "suggested_styles": [
          "Realistic",
          "Cinematic live-action",
          "Anime",
          "3D animation",
          "2D animation",
          "Documentary",
          "Commercial",
          "Film noir",
          "Fantasy",
          "Horror",
          "Vintage",
          "User-defined style"
        ]
      },
      {
        "step": 9,
        "name": "Collect Remaining Project Details",
        "required_fields": [
          "Project title",
          "Story or concept",
          "Total duration",
          "Aspect ratio",
          "Target platform",
          "Visual style",
          "Genre",
          "Tone",
          "Spoken-content format",
          "Dialogue or voice-over language",
          "Target audience",
          "Character descriptions",
          "Character appearances",
          "Character clothing",
          "Character personalities",
          "Location",
          "Time of day",
          "Weather when relevant",
          "Lighting style",
          "Color mood",
          "Preferred ending",
          "Call to action when relevant",
          "Preferred video model when provided"
        ],
        "instructions": [
          "Ask only for missing information.",
          "Do not repeat questions already answered.",
          "Do not ask for music preferences because background music is prohibited.",
          "Ask about preferred natural ambience or organic sound only when necessary."
        ]
      },
      {
        "step": 10,
        "name": "Select and Confirm Video Model",
        "preferred_model": "seedance 2.5",
        "fallback_model": "seedance 2.0",
        "instructions": [
          "Use seedance 2.5 when it is available and supported.",
          "If seedance 2.5 is requested but only seedance 2.0 is available, do not switch silently.",
          "Explain which model is available.",
          "Ask permission before switching to seedance 2.0.",
          "Continue only after user approval.",
          "Do not claim that a model was used unless it was actually available and selected."
        ]
      },
      {
        "step": 11,
        "name": "Select Generation Quota",
        "allowed_quota": [
          10,
          15
        ],
        "decision_factors": [
          "Total video duration",
          "Estimated number of clips",
          "Number of storyboard images",
          "Scene complexity",
          "Expected regenerations",
          "Available generation limits"
        ],
        "instructions": [
          "Recommend either quota 10 or quota 15.",
          "Explain why the quota is suitable.",
          "Tell the user when quota limitations may restrict clips or revisions."
        ]
      },
      {
        "step": 12,
        "name": "Obtain Storyboard Reference",
        "assistant_message": "Please send your storyboard image sample.",
        "instructions": [
          "Do not ask again when a reference image is already available in the current project.",
          "Analyze the existing reference when already uploaded."
        ]
      },
      {
        "step": 13,
        "name": "Analyze Storyboard Reference",
        "use_reference_for": [
          "Overall layout",
          "Panel organization",
          "Grid structure",
          "Typography hierarchy",
          "Borders",
          "Headings",
          "Timecode placement",
          "Shot-title placement",
          "Camera notes",
          "Action notes",
          "Dialogue notes",
          "Voice-over notes",
          "Sound notes",
          "Bottom information sections",
          "Visual density",
          "Professional production-board appearance"
        ],
        "do_not_copy": [
          "Original story",
          "Original characters",
          "Faces",
          "Dialogue",
          "Scene content",
          "Branding",
          "Logos",
          "Copyrighted visual elements"
        ],
        "adaptive_layout": {
          "enabled": true,
          "adjustable_elements": [
            "Number of panels",
            "Rows",
            "Columns",
            "Number of storyboard sheets",
            "Panel dimensions",
            "Text density",
            "Timecode ranges"
          ],
          "rule": "Preserve the design language of the reference but do not force the new story into the reference's original number of panels."
        }
      },
      {
        "step": 14,
        "name": "Analyze Story Structure",
        "identify": [
          "Beginning",
          "Setup",
          "Main objective",
          "Conflict",
          "Emotional progression",
          "Important visual moments",
          "Dialogue moments",
          "Voice-over moments",
          "Turning point",
          "Climax",
          "Resolution",
          "Ending",
          "Call to action when relevant"
        ],
        "instructions": [
          "Remove unnecessary repetition while preserving the user's meaning.",
          "Do not change the central message without permission.",
          "Plan natural environmental sound for every scene.",
          "Do not plan any background music."
        ]
      },
      {
        "step": 15,
        "name": "Create Adaptive Clip Breakdown",
        "automatic_decisions": [
          "Total number of clips",
          "Duration of each clip",
          "Start and end timecodes",
          "Scene boundaries",
          "Camera coverage",
          "Action timing",
          "Dialogue timing",
          "Voice-over timing",
          "Organic sound progression",
          "Transition timing"
        ],
        "duration_factors": [
          "Dialogue length",
          "Voice-over word count",
          "Speaking pace",
          "Character action",
          "Camera movement",
          "Scene complexity",
          "Emotional importance",
          "Reaction timing",
          "Suspense timing",
          "Environmental sound timing",
          "Transition requirements",
          "Model-supported clip lengths"
        ],
        "short_clip_uses": [
          "Reactions",
          "Inserts",
          "Fast action",
          "Visual reveals",
          "Cutaways",
          "Detail shots",
          "Transitions",
          "Sudden emotional moments"
        ],
        "long_clip_uses": [
          "Dialogue",
          "Voice-over passages",
          "Emotional performance",
          "Establishing shots",
          "Slow camera movements",
          "Complex physical action",
          "Important narrative information"
        ],
        "validation": [
          "All clip durations must add up exactly to the selected total duration.",
          "No important action may be cut off.",
          "Dialogue and voice-over must fit naturally.",
          "Camera movement must be completed or intentionally continued.",
          "Every clip must function as a practical video-generation unit.",
          "Every clip ending must connect with the next clip opening.",
          "Clip pacing must match the genre and platform."
        ]
      },
      {
        "step": 16,
        "name": "Calculate Speech Timing",
        "speech_speed_guidelines_wpm": {
          "slow_emotional": {
            "minimum": 90,
            "maximum": 115
          },
          "natural_conversational": {
            "minimum": 120,
            "maximum": 150
          },
          "energetic_delivery": {
            "minimum": 150,
            "maximum": 175
          }
        },
        "instructions": [
          "Treat these speeds as planning ranges rather than fixed limits.",
          "Adjust for pauses, emotion, breathing, reactions and pronunciation difficulty.",
          "Do not fill every second with speech.",
          "Reserve time for organic environmental sound.",
          "Do not reserve time for background music."
        ]
      },
      {
        "step": 17,
        "name": "Design Clip-to-Clip Continuity",
        "required_for_each_clip": {
          "opening_state": [
            "Opening frame",
            "Camera position",
            "Camera movement",
            "Character position",
            "Character pose",
            "Character expression",
            "Prop positions",
            "Lighting",
            "Weather",
            "Active ambience",
            "Action state"
          ],
          "ending_state": [
            "Final frame",
            "Camera position",
            "Camera movement",
            "Character position",
            "Character pose",
            "Character expression",
            "Prop positions",
            "Lighting",
            "Weather",
            "Active ambience",
            "Action state"
          ],
          "next_clip_match": [
            "State exactly how the next clip starts from the previous clip's ending.",
            "Specify whether the connection uses match-on-action, composition match, eyeline match, movement continuation or sound bridge.",
            "Carry organic ambience across the cut when location and time remain unchanged."
          ]
        },
        "instructions": [
          "The last frame of Clip 1 must visually and sonically match the first frame of Clip 2.",
          "Apply the same rule to every consecutive clip pair.",
          "Do not reset character movement or object positions.",
          "Do not reverse screen direction without an intentional motivated transition.",
          "Do not change weather, lighting or ambience between connected clips.",
          "When a time or location jump is required, clearly label it as an intentional transition."
        ]
      },
      {
        "step": 18,
        "name": "Present Project Plan",
        "approval_required": true,
        "sections": {
          "project_summary": [
            "Title",
            "Story",
            "Duration",
            "Aspect ratio",
            "Platform",
            "Style",
            "Genre",
            "Tone",
            "Model",
            "Quota"
          ],
          "audio_strategy": [
            "Spoken-content format",
            "Narrator gender when applicable",
            "Narrator voice age",
            "Narrator delivery style",
            "Character voice profiles",
            "Dialogue language",
            "Organic sound direction",
            "Confirmation that no background music will be used"
          ],
          "clip_plan": [
            "Clip number",
            "Clip title",
            "Start time",
            "End time",
            "Duration",
            "Scene purpose",
            "Location",
            "Characters",
            "Camera framing",
            "Camera movement",
            "Main action",
            "Facial expression",
            "Dialogue",
            "Voice-over",
            "Voice tone",
            "Organic ambience",
            "Foley and sound effects",
            "Transition",
            "Ending-frame state",
            "Next-clip opening match",
            "Continuity notes"
          ]
        },
        "instructions": [
          "Present the plan in Markdown.",
          "Ask for approval before storyboard generation.",
          "Skip approval only when the user explicitly enables automatic generation."
        ]
      },
      {
        "step": 19,
        "name": "Create Written Storyboard",
        "per_clip_template": {
          "clip_number": "Clip number",
          "clip_title": "Short descriptive title",
          "time": "Start time to end time",
          "duration": "Clip duration in seconds",
          "camera": "Shot size, angle, lens perspective, framing and movement",
          "visual": "Exact frame composition and environment",
          "action": "Physical action in chronological order",
          "expression": "Facial expression and body language",
          "dialogue": "Speaker and exact dialogue, or None",
          "voice_over": "Exact narration, or None",
          "voice_profile": "Gender, voice age, tone, pace, emotion and delivery",
          "organic_ambience": "Location-specific natural environmental sound",
          "foley": "Footsteps, fabric, objects, breathing and other physical sounds",
          "background_music": "None",
          "transition": "Visual transition and organic sound bridge",
          "opening_state": "Exact opening frame and audio state",
          "ending_state": "Exact ending frame and audio state",
          "next_clip_match": "How the ending connects to the next clip opening",
          "continuity": "Character, wardrobe, prop, lighting, position and screen-direction notes",
          "video_prompt": "Complete generation-ready prompt"
        },
        "story_requirements": [
          "Clear beginning",
          "Narrative development",
          "Emotional progression",
          "Climax",
          "Resolution",
          "Ending or call to action"
        ]
      },
      {
        "step": 20,
        "name": "Generate Storyboard Visuals",
        "condition": "Run only after written storyboard approval.",
        "character_continuity": [
          "Face",
          "Age",
          "Skin tone",
          "Hairstyle",
          "Body type",
          "Clothing",
          "Accessories",
          "Character proportions",
          "Emotional progression"
        ],
        "location_continuity": [
          "Architecture",
          "Furniture",
          "Props",
          "Background",
          "Weather",
          "Time of day",
          "Lighting direction",
          "Color temperature",
          "Spatial relationships"
        ],
        "design_requirements": [
          "Professional film-production layout",
          "Balanced panel grid",
          "Clear clip numbering",
          "Readable titles",
          "Readable timecodes",
          "Camera information",
          "Action information",
          "Dialogue information",
          "Voice-over information",
          "Organic sound information",
          "Clip continuity information"
        ],
        "prohibited": [
          "Duplicate scenes",
          "Unrequested characters",
          "Random face changes",
          "Random wardrobe changes",
          "Random location changes",
          "Distorted anatomy",
          "Unreadable invented text",
          "Background music instructions"
        ]
      },
      {
        "step": 21,
        "name": "Handle Storyboard Text Limitations",
        "condition": "Run when accurate text cannot be rendered inside storyboard images.",
        "outputs": [
          "Clean storyboard visuals with empty, minimal or numbered text areas",
          "Separate structured Markdown storyboard with exact text",
          "Panel-placement guide for Canva, Photoshop, Figma or another design application"
        ],
        "rule": "Do not claim that text inside an image is accurate when it is visibly incorrect."
      },
      {
        "step": 22,
        "name": "Obtain Final Storyboard Approval",
        "required_confirmations": [
          "Storyboard approval",
          "Clip timing approval",
          "Voice-over or dialogue strategy approval",
          "Organic sound strategy approval",
          "Confirmation of no background music",
          "Model confirmation",
          "Aspect-ratio confirmation",
          "Clip-to-clip continuity approval"
        ],
        "automatic_mode_exception": "Skip only when the user explicitly authorizes automatic generation."
      },
      {
        "step": 23,
        "name": "Generate Video Clips",
        "generation_order": "chronological",
        "per_clip_requirements": [
          "Use the approved storyboard frame.",
          "Use the approved video prompt.",
          "Use the assigned adaptive duration.",
          "Use the approved aspect ratio.",
          "Use the approved visual style.",
          "Maintain character continuity.",
          "Maintain wardrobe continuity.",
          "Maintain location continuity.",
          "Maintain lighting continuity.",
          "Preserve screen direction.",
          "Preserve eyelines.",
          "Match character and prop positions.",
          "Keep camera motion physically plausible.",
          "Complete or naturally continue the required action.",
          "Create a transition-compatible ending frame.",
          "Match the previous clip's ending when generating the next clip.",
          "Do not add background music."
        ],
        "continuity_generation_instruction": "For Clip 2 and every later clip, provide the previous clip's final frame or an exact final-frame description as the starting continuity reference whenever the generation system supports it."
      },
      {
        "step": 24,
        "name": "Generate Voice-over and Dialogue",
        "voice_over_rules": [
          "Use the approved narrator gender.",
          "Maintain the same narrator identity.",
          "Apply the assigned tone for each clip.",
          "Match the emotional progression.",
          "Preserve natural pauses.",
          "Synchronize narration with visuals.",
          "Do not add music beneath narration."
        ],
        "dialogue_rules": [
          "Maintain each character's voice identity.",
          "Match dialogue emotion to performance.",
          "Keep timing natural.",
          "Synchronize reactions and pauses.",
          "Use lip synchronization when supported.",
          "Do not add music beneath dialogue."
        ],
        "combined_voice_and_dialogue_rules": [
          "Prevent unnecessary overlap.",
          "Lower or pause narration during important dialogue.",
          "Use narration to connect scenes instead of repeating dialogue.",
          "Preserve speech intelligibility."
        ]
      },
      {
        "step": 25,
        "name": "Create Organic Sound Design",
        "background_music": "prohibited",
        "per_clip_sound_layers": [
          "Primary environmental ambience",
          "Secondary environmental details",
          "Character Foley",
          "Object sounds",
          "Weather sounds when relevant",
          "Dialogue",
          "Voice-over",
          "Breathing and movement",
          "Natural sound bridge"
        ],
        "mixing_rules": [
          "Keep sound realistic and clean.",
          "Use location-appropriate ambience.",
          "Keep dialogue and voice-over clear.",
          "Avoid excessive loudness.",
          "Avoid artificial cinematic effects unless they occur physically in the scene.",
          "Carry ambience between connected clips.",
          "Change ambience only when location, time or weather changes.",
          "Use silence when the natural scene would be quiet.",
          "Never insert background music."
        ]
      },
      {
        "step": 26,
        "name": "Perform Per-Clip Quality Control",
        "checks": [
          "Correct duration",
          "Correct aspect ratio",
          "Character consistency",
          "Face consistency",
          "Wardrobe consistency",
          "Location consistency",
          "Lighting consistency",
          "Prop continuity",
          "Screen direction",
          "Camera continuity",
          "Action completion",
          "Dialogue timing",
          "Voice-over timing",
          "Voice identity consistency",
          "Voice tone accuracy",
          "Lip synchronization when applicable",
          "Natural ambience accuracy",
          "Organic sound quality",
          "No background music",
          "Ending-frame match with next clip",
          "Ambient-sound match with next clip",
          "Transition compatibility",
          "Visual artifacts",
          "Audio artifacts"
        ],
        "failure_handling": [
          "Revise only the failed clip when possible.",
          "Regenerate adjacent clips only when continuity is affected.",
          "Do not regenerate the complete project unless necessary."
        ]
      },
      {
        "step": 27,
        "name": "Assemble Final Video",
        "assembly_requirements": [
          "Place clips in chronological order.",
          "Preserve approved durations.",
          "Apply approved transitions.",
          "Match every clip ending with the next clip beginning.",
          "Synchronize dialogue.",
          "Synchronize voice-over.",
          "Synchronize organic ambience.",
          "Synchronize Foley and physical sound effects.",
          "Use natural sound bridges.",
          "Balance audio levels.",
          "Match color and exposure between clips.",
          "Preserve total duration.",
          "Remove accidental gaps.",
          "Remove duplicated frames.",
          "Do not add background music during editing or export."
        ]
      },
      {
        "step": 28,
        "name": "Export Video",
        "target_bitrate_mbps": 12,
        "instructions": [
          "Target 12 Mbps when the selected tool, codec and export process support bitrate control.",
          "When direct bitrate control is unavailable, use the highest appropriate quality setting.",
          "Do not falsely claim that the output is exactly 12 Mbps.",
          "Transcode to approximately 12 Mbps when post-processing is available.",
          "Preserve resolution, frame rate, aspect ratio, audio synchronization and visual quality."
        ]
      },
      {
        "step": 29,
        "name": "Final Project Quality Control",
        "full_sequence_checks": [
          "Total duration is correct.",
          "All clips are in the correct order.",
          "Every clip ending matches the following clip opening.",
          "Actions continue naturally.",
          "Character positions remain continuous.",
          "Screen direction remains consistent.",
          "Camera movement remains coherent.",
          "Weather and lighting remain consistent.",
          "Organic ambience remains continuous.",
          "Dialogue and voice-over remain synchronized.",
          "No clip contains background music.",
          "Audio is organic, neat and realistic.",
          "Export settings are correct.",
          "No clip or frame is missing."
        ]
      },
      {
        "step": 30,
        "name": "Present Final Delivery Summary",
        "format": "markdown",
        "include": [
          "Project title",
          "Final total duration",
          "Number of clips",
          "Aspect ratio",
          "Video model",
          "Quota used",
          "Spoken-content format",
          "Narrator gender when applicable",
          "Dialogue language",
          "Visual style",
          "Storyboard status",
          "Clip continuity status",
          "Organic sound status",
          "Confirmation that no background music was used",
          "Video-generation status",
          "Voice-over status",
          "Dialogue status",
          "Final assembly status",
          "Export status",
          "Quality-control results",
          "Limitations or failed generations"
        ]
      }
    ],
    "global_storyboard_rules": {
      "adaptive_panel_count": true,
      "fixed_clip_duration": false,
      "fixed_number_of_clips": false,
      "agent_decides_clip_duration": true,
      "agent_decides_clip_count": true,
      "all_clip_durations_must_equal_total_duration": true,
      "include_timecodes": true,
      "include_camera_details": true,
      "include_action_details": true,
      "include_expression_details": true,
      "include_dialogue": true,
      "include_voice_over": true,
      "include_organic_sound": true,
      "include_background_music": false,
      "include_opening_state": true,
      "include_ending_state": true,
      "include_next_clip_match": true,
      "include_continuity_notes": true,
      "include_generation_prompt": true
    },
    "global_prohibitions": [
      "Do not use background music in any clip.",
      "Do not use cinematic score in any clip.",
      "Do not use musical transition effects.",
      "Do not silently change the selected video model.",
      "Do not force equal clip durations.",
      "Do not force a fixed number of storyboard panels.",
      "Do not generate unrelated standalone clips.",
      "Do not reset actions between clips.",
      "Do not change character appearance without narrative reason.",
      "Do not change wardrobe without narrative reason.",
      "Do not change the location without narrative reason.",
      "Do not change ambience between clips occurring in the same location and time.",
      "Do not repeat dialogue across clip boundaries.",
      "Do not claim successful generation when generation failed.",
      "Do not claim exact bitrate control when it was unavailable."
    ],
    "required_markdown_headings": [
      "Step 1: Story",
      "Step 2: Spoken-content format",
      "Step 3: Video duration",
      "Step 4: Aspect ratio",
      "Step 5: Visual style",
      "Step 6: Project details",
      "Step 7: Model selection",
      "Step 8: Quota selection",
      "Step 9: Storyboard reference",
      "Step 10: Story analysis",
      "Step 11: Adaptive clip breakdown",
      "Step 12: Voice and dialogue strategy",
      "Step 13: Organic sound strategy",
      "Step 14: Clip continuity map",
      "Step 15: Storyboard plan",
      "Step 16: Storyboard generation",
      "Step 17: Storyboard approval",
      "Step 18: Video generation",
      "Step 19: Voice-over and dialogue",
      "Step 20: Organic sound design",
      "Step 21: Quality control",
      "Step 22: Final assembly and export"
    ],
    "truthfulness_policy": {
      "never_claim_completion_without_success": [
        "Skill loading",
        "Model selection",
        "Model switching",
        "Storyboard generation",
        "Image generation",
        "Video generation",
        "Voice-over generation",
        "Dialogue generation",
        "Lip synchronization",
        "Organic sound generation",
        "Clip continuity verification",
        "Bitrate control",
        "Final assembly",
        "Export",
        "Quality-control verification"
      ]
    },
    "initial_response": "Please provide your story idea or complete story."
  }
}`;
    const CUSTOM_PROMPT_STORAGE_KEY = 'doubao-assistant-custom-prompt';
    let activeSeedancePrompt = SEEDANCE_SYSTEM_PROMPT;
    let activeSeedancePromptMarker = SEEDANCE_SYSTEM_PROMPT_MARKER;

    function deriveMarkerFromPrompt(text) {
        const trimmed = (text || '').trim();
        return trimmed.slice(0, 40) || SEEDANCE_SYSTEM_PROMPT_MARKER;
    }

    function setActiveSeedancePrompt(text) {
        const trimmed = (text || '').trim();
        if (!trimmed) return false;
        activeSeedancePrompt = trimmed;
        activeSeedancePromptMarker = deriveMarkerFromPrompt(trimmed);
        return true;
    }
    let seedanceDurationEnabled = false;
    let seedanceTargetDuration = 15;

    function isDoubaoPage() {
        return isDoubaoHost(pageWindow.location.hostname);
    }

    function isDoubaoHost(hostname) {
        return hostname.includes('doubao.com') || hostname.includes('dola.com');
    }

    function getDoubaoOrigin() {
        return isDoubaoPage() ? pageWindow.location.origin : 'https://www.doubao.com';
    }

    function readStoredValue(key) {
        try {
            return pageWindow.localStorage?.getItem(key) ?? null;
        } catch (error) {
            return null;
        }
    }

    function writeStoredValue(key, value) {
        try {
            pageWindow.localStorage?.setItem(key, value);
        } catch (error) {
        }
    }

    function loadCustomPromptFromStorage() {
        const stored = readStoredValue(CUSTOM_PROMPT_STORAGE_KEY);
        if (stored && stored.trim()) {
            setActiveSeedancePrompt(stored);
        }
    }

    function saveCustomPromptToStorage(text) {
        writeStoredValue(CUSTOM_PROMPT_STORAGE_KEY, text);
    }

    function loadSeedanceDurationConfig() {
        const enabledValue = readStoredValue(SEEDANCE_DURATION_ENABLED_KEY);
        const durationValue = parseInt(readStoredValue(SEEDANCE_DURATION_VALUE_KEY) || '15', 10);
        seedanceDurationEnabled = enabledValue === 'on';
        seedanceTargetDuration = VALID_SEEDANCE_DURATIONS.has(durationValue) ? durationValue : 15;
    }

    function persistSeedanceDurationConfig() {
        writeStoredValue(SEEDANCE_DURATION_ENABLED_KEY, seedanceDurationEnabled ? 'on' : 'off');
        writeStoredValue(SEEDANCE_DURATION_VALUE_KEY, String(seedanceTargetDuration));
    }

    function setSeedanceDurationConfig(duration, enabled = seedanceDurationEnabled) {
        const previousDuration = seedanceTargetDuration;
        seedanceDurationEnabled = Boolean(enabled);
        if (VALID_SEEDANCE_DURATIONS.has(duration)) {
            seedanceTargetDuration = duration;
        }
        persistSeedanceDurationConfig();
        if (previousDuration === 15 && seedanceTargetDuration !== 15) {
            clearPatchedDurationLabels();
        }
        if (!seedanceDurationEnabled) {
            clearPatchedDurationLabels();
            document.querySelectorAll('.seedance-15s-injected').forEach(option => option.remove());
        } else if (seedanceTargetDuration === 15) {
            patchDurationTriggerLabels();
        }
    }

    function modifySeedanceRequestBody(bodyText) {
        if (!isDoubaoPage() || !seedanceDurationEnabled || typeof bodyText !== 'string' || !bodyText.includes('ability_param')) {
            return bodyText;
        }

        const targetDuration = seedanceTargetDuration;
        const directPattern = /(\\*)"duration(\\*)"\s*:\s*(\d+)/g;
        let matched = false;
        const replaced = bodyText.replace(directPattern, (match, leftEscape, rightEscape) => {
            matched = true;
            return `${leftEscape}"duration${rightEscape}":${targetDuration}`;
        });

        if (matched) {
            return replaced;
        }

        const fallback = bodyText.replace(
            /(ability_param[\s\S]*?duration\\*"\s*:\s*)(\d+)/g,
            `$1${targetDuration}`
        );
        return fallback;
    }

    function updateTopicFromRequestBody(bodyText) {
        if (typeof bodyText !== 'string') return;
        try {
            const payload = JSON.parse(bodyText);
            currentTopicContext = createTopicContext(payload);
        } catch (error) {
        }
    }

    loadSeedanceDurationConfig();
    loadCustomPromptFromStorage();

    window.addEventListener('DOLA_SET_SEEDANCE_CONFIG', (event) => {
        const detail = event.detail || {};
        const duration = Number(detail.duration) || 15;
        const enabled = detail.enabled !== false;
        setSeedanceDurationConfig(duration, enabled);
    });

    window.addEventListener('DOLA_GET_CHAT_MEDIA', () => {
        window.dispatchEvent(new CustomEvent('DOLA_CHAT_MEDIA_RESPONSE', {
            detail: {
                videos: chatVideos || [],
                images: chatImages || []
            }
        }));
    });

    function normalizeImageUrl(url) {
        if (typeof url !== 'string') return '';

        const normalizedUrl = url.replace(/&amp;/g, '&');
        if (pageWindow.location.protocol !== 'https:' || !normalizedUrl.startsWith('http://')) {
            return normalizedUrl;
        }

        try {
            const parsedUrl = new URL(normalizedUrl);
            if (isDoubaoHost(parsedUrl.hostname)) {
                parsedUrl.protocol = 'https:';
                return parsedUrl.href;
            }
        } catch (error) {
            // Keep malformed or non-standard URLs unchanged for existing fallback handling.
        }
        return normalizedUrl;
    }

    function findConversationField(value, keys, depth = 0, seen = new Set()) {
        if (!value || typeof value !== 'object' || depth > 8 || seen.has(value)) return '';
        seen.add(value);
        if (Array.isArray(value)) {
            for (const item of value) {
                const result = findConversationField(item, keys, depth + 1, seen);
                if (result) return result;
            }
            return '';
        }
        for (const key of keys) {
            const candidate = value[key];
            if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
        }
        for (const child of Object.values(value)) {
            const result = findConversationField(child, keys, depth + 1, seen);
            if (result) return result;
        }
        return '';
    }

    function getConversationRouteId() {
        const match = pageWindow.location.pathname.match(/\/(chat|thread)\/([^/?#]+)/i);
        if (match?.[2]) return match[2];
        const params = new URLSearchParams(pageWindow.location.search);
        return params.get('conversation_id') || params.get('session_id') || pageWindow.location.pathname;
    }

    function getPageConversationTitle() {
        const title = String(document.title || '')
            .replace(/[-_|]\s*(豆包|Doubao).*$/i, '')
            .replace(/^豆包\s*[-_|]?\s*/i, '')
            .trim();
        return title && !/^豆包$/i.test(title) ? title : '';
    }

    function createTopicContext(source) {
        const value = source && typeof source === 'object' ? source : {};
        const sourceId = findConversationField(value, [
            'conversation_id', 'conversationId', 'session_id', 'sessionId', 'section_id', 'sectionId'
        ]);
        const routeId = getConversationRouteId();
        const currentId = currentTopicContext.id?.startsWith('conversation-')
            ? currentTopicContext.id.slice('conversation-'.length)
            : '';
        const id = sourceId || (!['/chat/', '/thread/'].includes(routeId) ? routeId : currentId) || routeId;
        let title = findConversationField(value, [
            'conversation_title', 'conversationTitle', 'conversation_name', 'session_name', 'topic_name', 'title'
        ]);
        if (!title && currentId === id && !currentTopicContext.isFallback) title = currentTopicContext.title;
        if (!title) title = getPageConversationTitle();
        title = title.replace(/\s+/g, ' ').trim();
        if (title.length > 34) title = `${title.slice(0, 34)}...`;
        const shortId = String(id || 'current').slice(-8);
        return { id: `conversation-${id || 'current'}`, title: title || `Conversation ${shortId}`, isFallback: !title };
    }

    function refineTopicFromCreation(creation, topic) {
        return topic;
    }

    function rebuildChatImageIndex(images = chatImages) {
        chatImageIndex.clear();
        for (const image of images) {
            const url = normalizeImageUrl(image?.url);
            if (!url) continue;
            image.url = url;
            chatImageIndex.set(url, image);
        }
    }

    function replaceChatImages(images) {
        chatImages = Array.isArray(images) ? images : [];
        rebuildChatImageIndex(chatImages);
    }

    function addChatImage(imageInfo) {
        const url = normalizeImageUrl(imageInfo?.url);
        if (!url) return false;

        const width = imageInfo.width || 0;
        const height = imageInfo.height || 0;
        const existingImage = chatImageIndex.get(url);
        if (existingImage) {
            if (!existingImage.width && width) existingImage.width = width;
            if (!existingImage.height && height) existingImage.height = height;
            if (!existingImage.previewUrl && imageInfo.previewUrl) existingImage.previewUrl = imageInfo.previewUrl;
            if (imageInfo.topic && (!existingImage.topicId || existingImage.topicId === 'uncategorized')) {
                existingImage.topicId = imageInfo.topic.id;
                existingImage.topicTitle = imageInfo.topic.title;
            }
            return false;
        }

        const topic = imageInfo.topic || currentTopicContext;
        const image = {
            url,
            previewUrl: normalizeImageUrl(imageInfo.previewUrl),
            width,
            height,
            topicId: topic.id,
            topicTitle: topic.title
        };
        chatImages.push(image);
        chatImageIndex.set(url, image);
        return true;
    }

    function getUrlInfo(value) {
        if (typeof value === 'string') return { url: normalizeImageUrl(value), width: 0, height: 0 };
        if (!value || typeof value !== 'object') return null;
        if (Array.isArray(value)) return value.map(getUrlInfo).find(Boolean) || null;
        const url = normalizeImageUrl(value.url || value.image_url || value.src || value.uri);
        return url ? { url, width: value.width || 0, height: value.height || 0 } : null;
    }

    function getCreationImageInfo(creation) {
        const image = creation?.image || {};
        const imageData = image.image_ori_raw;
        const previewData = [
            image.image_thumb,
            image.image_thumb_raw,
            image.image_thumbnail,
            image.image_thumb_url,
            image.thumbnail,
            image.thumb,
            image.thumb_url,
            image.preview_url,
            image.image_ori
        ].map(getUrlInfo).find(Boolean);
        if (typeof imageData === 'string') {
            return { url: imageData, previewUrl: previewData?.url || '', width: 0, height: 0 };
        }
        if (imageData && typeof imageData === 'object' && imageData.url) {
            return {
                url: imageData.url,
                previewUrl: previewData?.url || '',
                width: imageData.width || 0,
                height: imageData.height || 0
            };
        }
        return null;
    }

    function getCreationVideoPoster(creation) {
        const video = creation?.video || {};
        const candidates = [
            video.poster_url,
            video.poster,
            video.cover_url,
            video.cover,
            video.thumbnail,
            video.first_frame,
            creation?.poster_url,
            creation?.cover_url
        ];
        return candidates.map(getUrlInfo).find(Boolean)?.url || '';
    }

    function addChatVideo(videoInfo, topic = currentTopicContext) {
        if (!videoInfo || !videoInfo.url) return;
        const url = normalizeImageUrl(videoInfo.url);
        const vid = videoInfo.vid ? String(videoInfo.vid) : '';
        if ((vid && chatVideoVidIndex.has(vid)) || chatVideoUrlIndex.has(url)) {
            const existingVideo = chatVideos.find(video => (vid && String(video.vid) === vid) || video.url === url);
            if (existingVideo && topic && (!existingVideo.topicId || existingVideo.topicId === 'uncategorized')) {
                existingVideo.topicId = topic.id;
                existingVideo.topicTitle = topic.title;
            }
            return;
        }

        const normalizedVideoInfo = {
            ...videoInfo,
            url,
            topicId: videoInfo.topicId || topic.id,
            topicTitle: videoInfo.topicTitle || topic.title
        };
        chatVideos.push(normalizedVideoInfo);
        chatVideoUrlIndex.add(url);
        if (vid) {
            chatVideoVidIndex.add(vid);
        }
        try {
            window.dispatchEvent(new CustomEvent('DOLA_VIDEO_EXTRACTED', { detail: normalizedVideoInfo }));
        } catch (e) { }
    }

    const originalXHROpen = pageWindow.XMLHttpRequest.prototype.open;
    const originalXHRSend = pageWindow.XMLHttpRequest.prototype.send;

    pageWindow.XMLHttpRequest.prototype.open = function (method, url, ...args) {
        this._url = url;
        return originalXHROpen.apply(this, [method, url, ...args]);
    };

    pageWindow.XMLHttpRequest.prototype.send = function (...args) {
        const url = this._url;
        if (url && url.includes('/chat/completion') && typeof args[0] === 'string') {
            updateTopicFromRequestBody(args[0]);
            args[0] = modifySeedanceRequestBody(args[0]);
        }
        this.addEventListener('load', function () {
            if (url && (url.includes('/im/chain/single'))) {
                try {
                    const data = JSON.parse(this.responseText);
                    currentTopicContext = createTopicContext(data);
                    const messages = data?.downlink_body?.pull_singe_chain_downlink_body?.messages;
                    if (messages && Array.isArray(messages)) {
                        parseChatHistoryImages(messages);
                        processDoubaoFallbackVideos(data, this.responseText);
                    } else {
                        processDoubaoFallbackVideos(data, this.responseText);
                    }
                } catch (e) {
                }
            }
        });
        return originalXHRSend.apply(this, args);
    };
    const originalFetch = pageWindow.fetch;
    pageWindow.fetch = async function (...args) {
        const url = args[0];
        const requestUrl = typeof url === 'string' ? url : (url?.url || '');

        if (requestUrl && requestUrl.includes('/im/chain/single')) {
            const response = await originalFetch.apply(this, args);
            response.clone().text().then(text => {
                try {
                    const data = JSON.parse(text);
                    currentTopicContext = createTopicContext(data);
                    const messages = data?.downlink_body?.pull_singe_chain_downlink_body?.messages;
                    if (Array.isArray(messages)) {
                        parseChatHistoryImages(messages);
                    }
                    processDoubaoFallbackVideos(data, text);
                } catch (e) {
                }
            }).catch(() => { });
            return response;
        }

        if (requestUrl && requestUrl.includes('/chat/completion')) {
            if (args[1]?.body && typeof args[1].body === 'string') {
                updateTopicFromRequestBody(args[1].body);
                args[1].body = modifySeedanceRequestBody(args[1].body);
            }

            const response = await originalFetch.apply(this, args);
            if (response.status === 429) {
                try {
                    window.dispatchEvent(new CustomEvent('DOLA_QUOTA_EXHAUSTED', { detail: { status: 429, reason: 'HTTP 429 Quota Exceeded' } }));
                } catch (e) {}
            }

            if (!response.body || typeof response.body.getReader !== 'function') {
                return response;
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            const stream = new NativeReadableStream({
                async start(controller) {
                    let buffer = '';
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.includes('daily_limit') || line.includes('quota') || line.includes('limit_exceeded') || line.includes('达到今日')) {
                                try {
                                    window.dispatchEvent(new CustomEvent('DOLA_QUOTA_EXHAUSTED', { detail: { reason: 'Dola Stream Quota Exceeded' } }));
                                } catch (e) {}
                            }
                            if (line.startsWith('data: ')) {
                                try {
                                    const jsonStr = line.substring(6);
                                    if (jsonStr.includes('image_ori') || jsonStr.includes('fallback_api')) {
                                        const data = JSON.parse(jsonStr);
                                        if (jsonStr.includes('fallback_api')) {
                                            processDoubaoFallbackVideos(data, jsonStr);
                                        }
                                        if (data.event_data || data.patch_op) {
                                            parseStreamChunk(data);
                                        }
                                    }
                                } catch (e) { }
                            }
                        }

                        // Pass data to the original response
                        controller.enqueue(value);
                    }
                    controller.close();
                }
            });

            return new NativeResponse(stream, {
                headers: response.headers,
                status: response.status,
                statusText: response.statusText
            });
        }

        return originalFetch.apply(this, args);
    };
    function parseStreamChunk(data) {
        try {
            if (!data.event_data && !data.patch_op) {
                return;
            }

            let creations = [];
            let topic = currentTopicContext;

            if (data.patch_op) {

                for (const op of data.patch_op) {
                    if (
                        op.patch_value &&
                        Array.isArray(op.patch_value.content_block)
                    ) {
                        for (const block of op.patch_value.content_block) {
                            if (
                                block?.content?.creation_block &&
                                Array.isArray(block.content.creation_block.creations)
                            ) {
                                creations = block.content.creation_block.creations;
                                break;
                            }
                        }
                    }
                }

                if (creations.length === 0) {
                    const extPatch = data.patch_op.find(op =>
                        op.patch_value &&
                        typeof op.patch_value === 'object' &&
                        op.patch_value.ext?.creation_full_content
                    );

                    if (extPatch) {
                        try {
                            const creationFullContent = extPatch.patch_value.ext.creation_full_content;
                            const creationFullContent_obj = JSON.parse(creationFullContent);

                            for (const item of creationFullContent_obj) {
                                const content = item?.BlockInfo?.BlockContent?.content;
                                if (
                                    content &&
                                    typeof content === 'object' &&
                                    content.creation_block &&
                                    Array.isArray(content.creation_block.creations)
                                ) {
                                    creations = content.creation_block.creations;
                                    break;
                                }
                            }
                        } catch (e) { }
                    }
                }

            } else {
                let eventData;
                try {
                    eventData = JSON.parse(data.event_data);
                } catch (e) {
                    return;
                }

                if (!eventData.message?.content) {
                    return;
                }
                const detectedTopic = createTopicContext(eventData.message, chatImages.length + chatVideos.length);
                topic = detectedTopic.isFallback && currentTopicContext
                    ? { ...detectedTopic, title: currentTopicContext.title }
                    : detectedTopic;

                let messageContent;
                try {
                    messageContent = JSON.parse(eventData.message.content);
                } catch (e) {
                    return;
                }
                if (!messageContent.creations || !Array.isArray(messageContent.creations)) {
                    return;
                }

                creations = messageContent.creations;
            }



            for (const creation of creations) {
                const creationTopic = refineTopicFromCreation(creation, topic);
                if (creation?.video) {
                    handleDoubaoCreationVideo(creation, creationTopic);
                } else {
                    const imageInfo = getCreationImageInfo(creation);
                    if (imageInfo) addChatImage({ ...imageInfo, topic: creationTopic });
                }
            }
        } catch (e) { }
    }

    async function getDoubaoVideoInfo(vid) {
        if (!vid) {
            return null;
        }

        const params = {
            version_code: '20800',
            language: 'zh-CN',
            device_platform: 'web',
            aid: '497858',
            real_aid: '497858',
            pkg_type: 'release_version',
            device_id: '',
            pc_version: '2.51.7',
            region: '',
            sys_region: '',
            samantha_web: '1',
            'use-olympus-account': '1',
            web_tab_id: '',
        };

        const queryString = new URLSearchParams(params).toString();
        const apiUrl = `${getDoubaoOrigin()}/samantha/media/get_play_info?${queryString}`;

        try {
            const response = await fetch(apiUrl, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json',
                    'origin': getDoubaoOrigin(),
                },
                body: JSON.stringify({ key: vid }),
            });

            const result = await response.json();

            if (!result || !result.data) {
                return null;
            }

            const originalMediaInfo = result.data.original_media_info || {};
            const meta = originalMediaInfo.meta || {};

            const videoInfo = {
                vid: vid,
                width: meta.width || 0,
                height: meta.height || 0,
                definition: meta.definition || '',
                duration: meta.duration || 0,
                codec_type: meta.codec_type || '',
                poster_url: result.data.poster_url || '',
                url: originalMediaInfo.main_url || '',
            };
            return videoInfo;
        } catch (e) {
            return null;
        }
    }

    function handleDoubaoCreationVideo(creation, topic = currentTopicContext) {
        processDoubaoFallbackVideos(
            creation,
            safeJsonStringify(creation),
            topic,
            getCreationVideoPoster(creation)
        );
    }

    function safeJsonStringify(value) {
        try {
            return JSON.stringify(value) || '';
        } catch {
            return '';
        }
    }

    function processDoubaoFallbackVideos(json, rawBody = '', topic = currentTopicContext, posterUrl = '') {
        const fallbackApis = findDoubaoFallbackApis(json, rawBody);
        if (!fallbackApis.length) return;
        removeLegacyDoubaoVideos();
        for (const fallbackApi of fallbackApis) {
            if (posterUrl) fallbackVideoPosterIndex.set(fallbackApi, posterUrl);
            if (processedFallbackApis.has(fallbackApi)) continue;
            processedFallbackApis.add(fallbackApi);

            getDoubaoVideoInfoFromFallbackApi(fallbackApi)
                .then(info => {
                    if (info && !info.poster_url) {
                        info.poster_url = fallbackVideoPosterIndex.get(fallbackApi) || '';
                    }
                    addChatVideo(info, topic);
                })
                .catch(() => { });
        }
    }

    async function getDoubaoVideoInfoFromFallbackApi(fallbackApi) {
        const apiUrl = replaceQueryParams(fallbackApi, {
            channel: 'no',
            codec_type: '8',
            logo_type: 'unwatermarked',
        });

        const payload = await requestJson(apiUrl);
        const data = getVideoData(payload);
        const picked = pickMainUrlEntry(data);
        if (!picked?.token) {
            return null;
        }

        const videoUrl = await decodeMainUrl(picked.token, findKeySeedDeep(payload));
        if (!videoUrl) {
            return null;
        }

        const meta = picked.entry || {};
        return {
            vid: data.vid || data.video_id || meta.vid || meta.video_id || apiUrl,
            source: 'fallback_api',
            width: Number(meta.vwidth || meta.width || data.vwidth || data.width || 0),
            height: Number(meta.vheight || meta.height || data.vheight || data.height || 0),
            definition: meta.definition || data.definition || '',
            duration: Number(meta.duration || data.duration || 0),
            codec_type: meta.codec_type || data.codec_type || '',
            poster_url: data.poster_url || data.poster || '',
            url: videoUrl,
        };
    }

    function removeLegacyDoubaoVideos() {
        const nextVideos = chatVideos.filter(video => video?.source === 'fallback_api');
        if (nextVideos.length === chatVideos.length) return;

        chatVideos = nextVideos;
    }

    function requestJson(url) {
        return new Promise((resolve, reject) => {
            if (typeof GM_xmlhttpRequest === 'function') {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url,
                    headers: {
                        accept: 'application/json,text/plain,*/*',
                    },
                    onload: (response) => {
                        if (response.status < 200 || response.status >= 300) {
                            reject(new Error(`Request failed: ${response.status}`));
                            return;
                        }
                        try {
                            const body = response.responseText || response.response;
                            resolve(typeof body === 'string' ? JSON.parse(body) : body);
                        } catch (error) {
                            reject(error);
                        }
                    },
                    onerror: () => reject(new Error('Request failed')),
                    ontimeout: () => reject(new Error('Request timeout')),
                });
                return;
            }

            originalFetch.call(pageWindow, url, {
                method: 'GET',
                credentials: 'omit',
                headers: {
                    accept: 'application/json,text/plain,*/*',
                },
            })
                .then(response => response.json())
                .then(resolve)
                .catch(reject);
        });
    }

    function findDoubaoFallbackApis(json, rawBody = '') {
        const apis = new Set();

        for (const value of findValuesByKey(json, 'fallback_api')) {
            addFallbackApi(apis, value);
        }

        const body = typeof rawBody === 'string' ? rawBody : '';
        const patterns = [
            /fallback_api\\":\\"(.*?)\\"/g,
            /"fallback_api"\s*:\s*"([^"]+)"/g,
        ];

        for (const pattern of patterns) {
            let match = pattern.exec(body);
            while (match) {
                addFallbackApi(apis, decodeJsonEscapedFragment(match[1]));
                match = pattern.exec(body);
            }
        }

        return Array.from(apis);
    }

    function addFallbackApi(apis, value) {
        if (typeof value !== 'string' || !value) return;

        const url = decodeJsonEscapedFragment(value);
        if (isHttpUrl(url)) {
            apis.add(url);
        }
    }

    function decodeJsonEscapedFragment(value) {
        let text = value;
        for (let index = 0; index < 3; index++) {
            try {
                const decoded = JSON.parse(`"${text.replace(/"/g, '\\"')}"`);
                if (decoded === text) break;
                text = decoded;
            } catch {
                break;
            }
        }
        return text.replace(/\\u0026/g, '&').replace(/\\\//g, '/');
    }

    function replaceQueryParams(url, params) {
        const parsedUrl = new URL(url);
        for (const [key, value] of Object.entries(params)) {
            parsedUrl.searchParams.set(key, value);
        }
        return parsedUrl.toString();
    }

    function getVideoData(payload) {
        const videoInfo = payload?.video_info || payload?.data?.video_info || payload;
        const data = videoInfo?.data || videoInfo;
        return data && typeof data === 'object' ? data : {};
    }

    function pickMainUrlEntry(data) {
        const videoList = data?.video_list;
        const entries = videoList && typeof videoList === 'object' && Object.keys(videoList).length
            ? Object.values(videoList)
            : [data];
        let best = null;

        for (const entry of entries) {
            if (!entry || typeof entry !== 'object') continue;
            const token = entry.main_url || entry.play_url || '';
            if (typeof token !== 'string' || !token.trim()) continue;
            const score = Number(entry.bitrate || entry.real_bitrate || 0)
                + Number(entry.vwidth || entry.width || 0) * Number(entry.vheight || entry.height || 0);
            if (!best || score > best.score) {
                best = { token: token.trim(), score, entry };
            }
        }

        return best;
    }

    function findKeySeedDeep(value, depth = 0) {
        if (depth > 10 || value == null) return '';

        if (typeof value === 'string') {
            let match = value.match(/(?:^|[?&])key_seed=([^&"'<>\\\s]+)/i);
            if (match) return decodeURIComponent(match[1]);
            match = value.match(/["']key_seed["']\s*:\s*["']([^"']+)/i);
            return match ? decodeURIComponent(match[1]) : '';
        }

        if (typeof value !== 'object') return '';

        if (typeof value.key_seed === 'string' && value.key_seed.trim()) {
            return value.key_seed.trim();
        }

        for (const item of Object.values(value)) {
            const hit = findKeySeedDeep(item, depth + 1);
            if (hit) return hit;
        }

        return '';
    }

    async function decodeMainUrl(token, keySeed = '') {
        if (isHttpUrl(token)) return token;

        const plainUrl = tryDecodeBase64Url(token);
        if (plainUrl) return plainUrl;

        if (token.startsWith('qAAB') && keySeed) {
            return await decodeQaabToken(token, keySeed);
        }

        return '';
    }

    function tryDecodeBase64Url(token) {
        const bytes = base64DecodeLoose(token);
        if (!bytes) return '';
        const text = asciiUrlFromBytes(bytes);
        return isHttpUrl(text) ? text : '';
    }

    function base64DecodeLoose(text) {
        const input = String(text || '').trim();
        const variants = [
            input,
            input.replace(/[$@#]/g, char => ({ '$': '_', '@': '/', '#': '.' }[char])),
            input.replace(/[$@#]/g, char => ({ '$': '+', '@': '/', '#': '=' }[char])),
        ];
        const seen = new Set();

        for (const candidate of variants) {
            if (!candidate || seen.has(candidate)) continue;
            seen.add(candidate);
            try {
                const normalized = padBase64(candidate).replace(/-/g, '+').replace(/_/g, '/');
                const binary = atob(normalized);
                const bytes = new Uint8Array(binary.length);
                for (let index = 0; index < binary.length; index++) {
                    bytes[index] = binary.charCodeAt(index);
                }
                return bytes;
            } catch {
                // Try the next variant.
            }
        }

        return null;
    }

    function padBase64(text) {
        const pad = (4 - (text.length % 4)) % 4;
        return text + '='.repeat(pad);
    }

    function asciiUrlFromBytes(bytes) {
        if (!bytes || !bytes.length) return '';
        for (const byte of bytes) {
            if (byte !== 9 && byte !== 10 && byte !== 13 && (byte < 32 || byte > 126)) {
                return '';
            }
        }
        return new TextDecoder().decode(bytes);
    }

    async function decodeQaabToken(token, keySeed) {
        const data = base64DecodeLoose(token);
        const seed = base64DecodeLoose(keySeed);
        if (!data || !seed) return '';

        const digest1 = await crypto.subtle.digest('SHA-512', seed.slice(0, 32));
        const salt = hexToBytes(QAAB_SALT_HEX);
        const digest2Input = concatBytes(new Uint8Array(digest1), salt);
        const digest2 = new Uint8Array(await crypto.subtle.digest('SHA-512', digest2Input));
        const key = digest2.slice(0, 16);
        const iv = digest2.slice(16, 32);
        const attempts = [];

        if (data.length >= 4 && data[0] === 0xa8 && data[1] === 0x00 && data[2] === 0x01 && data[3] === 0x00) {
            attempts.push({ payload: data.slice(4), key, iv });
            attempts.push({ payload: data.slice(4), key: iv, iv: key });
            if (data.length > 36) {
                attempts.push({ payload: data.slice(36), key, iv: data.slice(20, 36) });
                attempts.push({ payload: data.slice(36), key, iv });
            }
        } else {
            attempts.push({ payload: data, key, iv });
        }

        for (const attempt of attempts) {
            const url = await decryptAesCbcUrl(attempt.payload, attempt.key, attempt.iv);
            if (url) return url;
        }

        return '';
    }

    async function decryptAesCbcUrl(payload, keyBytes, ivBytes) {
        if (!payload.length || payload.length % 16 !== 0) return '';

        try {
            const key = await crypto.subtle.importKey('raw', keyBytes, 'AES-CBC', false, ['decrypt']);
            const plain = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-CBC', iv: ivBytes }, key, payload));
            const direct = asciiUrlFromBytes(plain);
            if (isHttpUrl(direct)) return direct;
            const stripped = stripPkcs7(plain);
            const url = asciiUrlFromBytes(stripped);
            return isHttpUrl(url) ? url : '';
        } catch {
            return '';
        }
    }

    function stripPkcs7(bytes) {
        if (!bytes || !bytes.length) return new Uint8Array();
        const pad = bytes[bytes.length - 1];
        if (pad < 1 || pad > 16 || pad > bytes.length) return bytes;
        for (let index = bytes.length - pad; index < bytes.length; index++) {
            if (bytes[index] !== pad) return bytes;
        }
        return bytes.slice(0, bytes.length - pad);
    }

    function hexToBytes(hex) {
        const bytes = new Uint8Array(hex.length / 2);
        for (let index = 0; index < bytes.length; index++) {
            bytes[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
        }
        return bytes;
    }

    function concatBytes(first, second) {
        const bytes = new Uint8Array(first.length + second.length);
        bytes.set(first, 0);
        bytes.set(second, first.length);
        return bytes;
    }

    function findValuesByKey(value, targetKey) {
        const values = [];
        walkJsonAndStrings(value, (node) => {
            if (!node || typeof node !== 'object' || Array.isArray(node)) return;
            if (Object.prototype.hasOwnProperty.call(node, targetKey)) {
                values.push(node[targetKey]);
            }
        });
        return values;
    }

    function walkJsonAndStrings(value, visitor, seen = new Set()) {
        if (value == null) return;

        if (typeof value === 'string') {
            const parsed = parseJsonString(value);
            if (parsed !== null) {
                walkJsonAndStrings(parsed, visitor, seen);
            }
            return;
        }

        if (typeof value !== 'object' || seen.has(value)) return;

        seen.add(value);
        visitor(value);

        if (Array.isArray(value)) {
            for (const item of value) {
                walkJsonAndStrings(item, visitor, seen);
            }
            return;
        }

        for (const key of Object.keys(value)) {
            walkJsonAndStrings(value[key], visitor, seen);
        }
    }

    function parseJsonString(text) {
        const trimmed = text.trim();
        if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) {
            return null;
        }

        try {
            return JSON.parse(trimmed);
        } catch {
            return null;
        }
    }

    function isHttpUrl(url) {
        return typeof url === 'string' && /^https?:\/\//i.test(url);
    }

    function parseChatHistoryImages(messages) {
        if (!Array.isArray(messages)) return;

        try {
            let lastNamedTopic = null;
            for (const [messageIndex, item] of messages.entries()) {
                try {
                    const detectedTopic = createTopicContext(item, messageIndex);
                    if (!detectedTopic.isFallback) lastNamedTopic = detectedTopic;
                    const topic = detectedTopic.isFallback && lastNamedTopic
                        ? { ...detectedTopic, title: lastNamedTopic.title }
                        : detectedTopic;
                    for (const content of item.content_block) {
                        const creationBlock = content.content?.creation_block;
                        if (!creationBlock || !Array.isArray(creationBlock.creations)) continue;
                        for (const creation of creationBlock.creations) {
                            const creationTopic = refineTopicFromCreation(creation, topic);
                            if (creation?.video) {
                                handleDoubaoCreationVideo(creation, creationTopic);
                            } else {
                                const imageInfo = getCreationImageInfo(creation);
                                if (imageInfo) addChatImage({ ...imageInfo, topic: creationTopic });
                            }
                        }
                    }

                } catch (e) {
                    continue;
                }
            }
        } catch (e) { }
    }

    function extractSharePageImages() {
        try {
            const imageList = [];
            const imageUrlIndex = new Set();

            const addCreationMedia = (creation, topic) => {
                const creationTopic = refineTopicFromCreation(creation, topic);
                if (creation?.video) {
                    handleDoubaoCreationVideo(creation, creationTopic);
                    return;
                }

                const imageInfo = getCreationImageInfo(creation);
                if (!imageInfo) return;
                const imageUrl = normalizeImageUrl(imageInfo.url);

                if (imageUrl && !imageUrlIndex.has(imageUrl)) {
                    imageUrlIndex.add(imageUrl);
                    imageList.push({
                        url: imageUrl,
                        previewUrl: imageInfo.previewUrl || '',
                        width: imageInfo.width,
                        height: imageInfo.height,
                        topicId: creationTopic.id,
                        topicTitle: creationTopic.title
                    });
                }
            };

            const parseContentBlock = (block) => {
                const contentData = block.content_v2 || block.content;
                if (!contentData) return null;
                return typeof contentData === 'string' ? JSON.parse(contentData) : contentData;
            };

            const parseMessageSnapshot = (messageSnapshot) => {
                if (!Array.isArray(messageSnapshot)) return;

                let lastNamedTopic = null;
                for (const [messageIndex, message] of messageSnapshot.entries()) {
                    const detectedTopic = createTopicContext(message, messageIndex);
                    if (!detectedTopic.isFallback) lastNamedTopic = detectedTopic;
                    const topic = detectedTopic.isFallback && lastNamedTopic
                        ? { ...detectedTopic, title: lastNamedTopic.title }
                        : detectedTopic;
                    for (const block of message.content_block || []) {
                        try {
                            const contentData = parseContentBlock(block);
                            const creations = contentData?.creation_block?.creations;
                            if (!Array.isArray(creations)) continue;

                            for (const creation of creations) {
                                addCreationMedia(creation, topic);
                            }
                        } catch (e) {
                            continue;
                        }
                    }
                }
            };

            const parseRouterDataItem = (data) => {
                if (typeof data === 'object' && data?.data?.message_snapshot?.message_list) {
                    parseMessageSnapshot(data.data.message_snapshot.message_list);
                    return;
                }

                if (Array.isArray(data) && data.length) {
                    const routerDataFnArg = data[0]?.routerDataFnArgs?.[0];
                    if (!routerDataFnArg) return;

                    const routerData = typeof routerDataFnArg === 'string'
                        ? JSON.parse(routerDataFnArg)
                        : routerDataFnArg;
                    parseMessageSnapshot(routerData?.data?.message_snapshot?.message_list);
                }
            };

            const scriptElement = document.querySelector(
                'script[data-script-src="modern-run-router-data-fn"], script[data-script-src="modern-run-window-fn"][data-fn-name="mergeLoaderData"]'
            );
            if (scriptElement) {
                const dataFnArgs = scriptElement.getAttribute('data-fn-args');
                if (dataFnArgs) {
                    const jsonStr = dataFnArgs.replace(/&quot;/g, '"');
                    const jsonData = JSON.parse(jsonStr);
                    processDoubaoFallbackVideos(jsonData, jsonStr);

                    for (const data of jsonData) {
                        parseRouterDataItem(data);
                    }
                    return imageList;
                }
            }

            return [];
        } catch (error) {
            return [];
        }
    }

    function extractImages() {

        if (isDoubaoPage() && pageWindow.location.pathname.includes('/chat/')) {
            return chatImages;
        } else {
            const images = extractSharePageImages();
            replaceChatImages(images);
            return images;
        }
    }

    function extractVideos() {
        return chatVideos;
    }

    function createDownloadTask(url, filename) {
        const downloadUrl = normalizeImageUrl(url);
        let settled = false;
        let rejectDownload = null;
        let abortDownload = null;

        const promise = new Promise((resolve, reject) => {
            rejectDownload = reject;

            const finish = () => {
                if (settled) return;
                settled = true;
                resolve();
            };

            const fail = (error) => {
                if (settled) return;
                settled = true;
                reject(error);
            };

            if (typeof GM_download === 'function') {
                try {
                    const download = GM_download({
                        url: downloadUrl,
                        name: filename,
                        saveAs: false,
                        onload: finish,
                        onerror: fail,
                        ontimeout: () => fail(new Error('Download timeout')),
                    });

                    if (download && typeof download.abort === 'function') {
                        abortDownload = () => download.abort();
                    }
                } catch (error) {
                    fail(error);
                }
                return;
            }

            const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
            abortDownload = () => controller?.abort();

            fetch(downloadUrl, { signal: controller?.signal })
                .then(response => response.blob())
                .then(blob => {
                    const blobUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = blobUrl;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
                    finish();
                })
                .catch(fail);
        });

        return {
            promise,
            abort() {
                if (settled) return;
                settled = true;
                try {
                    if (abortDownload) abortDownload();
                } catch (error) { }
                rejectDownload(new Error('Download cancelled'));
            },
        };
    }

    async function downloadImage(url, filename) {
        try {
            await createDownloadTask(url, filename).promise;
            return true;
        } catch (error) {
            if (error?.message === 'Download cancelled') return false;
            alert('Download failed, please try again');
            return false;
        }
    }

    function getMediaExtension(url, type) {
        try {
            const pathname = new URL(url, pageWindow.location.href).pathname;
            const match = pathname.match(/\.([a-z0-9]{2,5})$/i);
            if (match) return `.${match[1].toLowerCase()}`;
        } catch (e) {
            // Ignore malformed URLs and fall back to a safe default extension.
        }
        return type === 'video' ? '.mp4' : '.png';
    }

    function getDownloadFilename(type, index, url, topicNumber = 1, topicItemNumber = index + 1) {
        const mediaType = type === 'video' ? 'video' : 'image';
        return `doubao_topic_${topicNumber}_${mediaType}_${topicItemNumber}${getMediaExtension(url, type)}`;
    }

    function fetchMediaBlob(url, signal) {
        const mediaUrl = normalizeImageUrl(url);
        if (typeof GM_xmlhttpRequest === 'function') {
            return new Promise((resolve, reject) => {
                const request = GM_xmlhttpRequest({
                    method: 'GET',
                    url: mediaUrl,
                    responseType: 'blob',
                    onload: response => {
                        if (response.status >= 200 && response.status < 300 && response.response) {
                            resolve(response.response);
                        } else {
                            reject(new Error(`Media request failed: ${response.status}`));
                        }
                    },
                    onerror: () => reject(new Error('Media request failed')),
                    ontimeout: () => reject(new Error('Media request timeout'))
                });
                signal?.addEventListener('abort', () => {
                    request?.abort?.();
                    reject(new DOMException('Download cancelled', 'AbortError'));
                }, { once: true });
            });
        }

        return fetch(mediaUrl, { signal }).then(response => {
            if (!response.ok) throw new Error(`Media request failed: ${response.status}`);
            return response.blob();
        });
    }

    function calculateCrc32(bytes) {
        let crc = 0xffffffff;
        for (const byte of bytes) {
            crc ^= byte;
            for (let bit = 0; bit < 8; bit++) {
                crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
            }
        }
        return (crc ^ 0xffffffff) >>> 0;
    }

    function getZipDosTime(date = new Date()) {
        const year = Math.max(1980, date.getFullYear());
        return {
            time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
            date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
        };
    }

    function createZipArchive(entries) {
        const encoder = new TextEncoder();
        const localParts = [];
        const centralParts = [];
        const stamp = getZipDosTime();
        let localOffset = 0;

        for (const entry of entries) {
            const nameBytes = encoder.encode(entry.name);
            const data = entry.data;
            const crc = calculateCrc32(data);
            const localHeader = new Uint8Array(30 + nameBytes.length);
            const localView = new DataView(localHeader.buffer);
            localView.setUint32(0, 0x04034b50, true);
            localView.setUint16(4, 20, true);
            localView.setUint16(6, 0x0800, true);
            localView.setUint16(8, 0, true);
            localView.setUint16(10, stamp.time, true);
            localView.setUint16(12, stamp.date, true);
            localView.setUint32(14, crc, true);
            localView.setUint32(18, data.length, true);
            localView.setUint32(22, data.length, true);
            localView.setUint16(26, nameBytes.length, true);
            localHeader.set(nameBytes, 30);
            localParts.push(localHeader, data);

            const centralHeader = new Uint8Array(46 + nameBytes.length);
            const centralView = new DataView(centralHeader.buffer);
            centralView.setUint32(0, 0x02014b50, true);
            centralView.setUint16(4, 20, true);
            centralView.setUint16(6, 20, true);
            centralView.setUint16(8, 0x0800, true);
            centralView.setUint16(10, 0, true);
            centralView.setUint16(12, stamp.time, true);
            centralView.setUint16(14, stamp.date, true);
            centralView.setUint32(16, crc, true);
            centralView.setUint32(20, data.length, true);
            centralView.setUint32(24, data.length, true);
            centralView.setUint16(28, nameBytes.length, true);
            centralView.setUint32(42, localOffset, true);
            centralHeader.set(nameBytes, 46);
            centralParts.push(centralHeader);
            localOffset += localHeader.length + data.length;
        }

        const centralSize = centralParts.reduce((total, part) => total + part.length, 0);
        const end = new Uint8Array(22);
        const endView = new DataView(end.buffer);
        endView.setUint32(0, 0x06054b50, true);
        endView.setUint16(8, entries.length, true);
        endView.setUint16(10, entries.length, true);
        endView.setUint32(12, centralSize, true);
        endView.setUint32(16, localOffset, true);
        return new Blob([...localParts, ...centralParts, end], { type: 'application/zip' });
    }

    function downloadBlob(blob, filename) {
        const blobUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
    }

    async function copyTextToClipboard(text) {
        const value = String(text || '').trim();
        if (!value) throw new Error('No content to copy');

        if (navigator.clipboard?.writeText && pageWindow.isSecureContext) {
            try {
                await navigator.clipboard.writeText(value);
                return;
            } catch (error) {
                // Fall through to execCommand for userscript/browser permission edge cases.
            }
        }

        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none';
        document.body.appendChild(textarea);
        textarea.select();
        textarea.setSelectionRange(0, value.length);
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('Browser denied clipboard write');
    }

    function isOwnElement(el) {
        if (!el) return false;
        return Boolean(el.closest?.('#dba-workspace'));
    }

    function isVisible(el) {
        if (!el || !el.isConnected || isOwnElement(el)) return false;
        const ownerWindow = el.ownerDocument?.defaultView || window;
        const style = ownerWindow.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
            && rect.top < ownerWindow.innerHeight && rect.left < ownerWindow.innerWidth;
    }

    function clearPatchedDurationLabels() {
        document.querySelectorAll('[data-seedance-patched="15s"]').forEach(el => {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.trim() === '15s') {
                    walker.currentNode.textContent = '10s';
                    break;
                }
            }
            delete el.dataset.seedancePatched;
        });
    }

    function patchDurationTriggerLabels() {
        if (!isDoubaoPage() || !seedanceDurationEnabled || seedanceTargetDuration !== 15 || !document.body) {
            return;
        }

        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                return node.textContent.trim() === '10s'
                    ? NodeFilter.FILTER_ACCEPT
                    : NodeFilter.FILTER_REJECT;
            }
        });

        let node;
        while ((node = walker.nextNode())) {
            const el = node.parentElement;
            if (!el) continue;
            if (el.closest('[role="menu"]') || el.closest('[role="menuitem"]')) continue;
            if (el.dataset.seedancePatched === '15s') continue;

            node.textContent = '15s';
            el.dataset.seedancePatched = '15s';
        }
    }

    function getDurationMenu() {
        const menus = document.querySelectorAll('[role="menu"]');
        for (const menu of menus) {
            const items = menu.querySelectorAll('[role="menuitem"]');
            const texts = Array.from(items).map(item => item.textContent.trim());
            if (texts.includes('5s') && texts.includes('10s')) {
                return menu;
            }
        }
        return null;
    }

    function moveMenuCheckmarkTo15s(durationMenu) {
        if (!seedanceDurationEnabled || seedanceTargetDuration !== 15) return;

        const items = durationMenu.querySelectorAll('[role="menuitem"]');
        let item10s = null;
        let item15s = null;

        for (const item of items) {
            const text = item.textContent.trim();
            if (text === '10s') item10s = item;
            if (text === '15s') item15s = item;
        }

        if (!item10s || !item15s) return;

        const check10s = item10s.querySelector('svg');
        const check15s = item15s.querySelector('svg');
        if (!check10s || check15s) return;

        function getElementPath(el, root) {
            const path = [];
            while (el && el !== root) {
                const parent = el.parentElement;
                if (!parent) break;
                path.unshift(Array.from(parent.children).indexOf(el));
                el = parent;
            }
            return path;
        }

        function findElementByPath(root, path) {
            let el = root;
            for (const index of path) {
                if (!el.children[index]) return null;
                el = el.children[index];
            }
            return el;
        }

        const svgPath = getElementPath(check10s, item10s);
        if (svgPath.length >= 2) {
            const targetParent = findElementByPath(item15s, svgPath.slice(0, -1));
            if (targetParent) {
                targetParent.appendChild(check10s.cloneNode(true));
                check10s.remove();
                return;
            }
        }

        item15s.appendChild(check10s.cloneNode(true));
        check10s.remove();
    }

    function inject15sDurationOption() {
        if (!isDoubaoPage() || !seedanceDurationEnabled) return;

        const durationMenu = getDurationMenu();
        if (!durationMenu) {
            patchDurationTriggerLabels();
            return;
        }

        if (durationMenu.querySelector('.seedance-15s-injected')) {
            moveMenuCheckmarkTo15s(durationMenu);
            patchDurationTriggerLabels();
            return;
        }

        const template = Array.from(durationMenu.querySelectorAll('[role="menuitem"]'))
            .find(item => item.textContent.trim() === '10s');
        if (!template) {
            patchDurationTriggerLabels();
            return;
        }

        const option15s = template.cloneNode(true);
        option15s.classList.add('seedance-15s-injected');

        const walker = document.createTreeWalker(option15s, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
            if (walker.currentNode.textContent.trim() === '10s') {
                walker.currentNode.textContent = '15s';
                break;
            }
        }

        option15s.querySelectorAll('svg').forEach(svg => svg.remove());
        option15s.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            template.click();
            setSeedanceDurationConfig(15, true);
            moveMenuCheckmarkTo15s(durationMenu);
            patchDurationTriggerLabels();
        });

        durationMenu.appendChild(option15s);
        moveMenuCheckmarkTo15s(durationMenu);
        patchDurationTriggerLabels();
    }

    function startDurationMenuObserver() {
        if (durationMenuObserver || !isDoubaoPage() || !document.documentElement) return;

        durationMenuObserver = new MutationObserver(() => {
            inject15sDurationOption();
        });
        durationMenuObserver.observe(document.documentElement, { childList: true, subtree: true });
        setTimeout(inject15sDurationOption, 1200);
    }

    let initRetryCount = 0;
    const MAX_RETRY = 10;
    const LAUNCHER_ICON_DATA_URL = 'data:image/png;base64,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';

    function createAssistantWorkspace() {
        // In-page floating button and right-side sliding panel removed as requested.
        // Seedance 15s duration config, observer, and network hooks remain fully active.
        loadSeedanceDurationConfig();
        uiInitialized = true;
        return;

        const icon = (name) => {
            const paths = {
                library: '<path d="m16 6 4 14H4L8 6"/><path d="M8 6h8"/><path d="M9 2h6l1 4H8l1-4Z"/><path d="m10 11 2 2 2-2"/>',
                close: '<path d="m18 6-12 12"/><path d="m6 6 12 12"/>',
                image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
                video: '<path d="m16 13 5 3V8l-5 3"/><rect width="13" height="14" x="3" y="5" rx="2"/>',
                download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
                archive: '<rect width="18" height="5" x="3" y="3" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
                copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
                check: '<path d="m20 6-11 11-5-5"/>',
                sparkles: '<path d="m12 3-1.9 5.1L5 10l5.1 1.9L12 17l1.9-5.1L19 10l-5.1-1.9L12 3Z"/><path d="M5 3v4"/><path d="M3 5h4"/><path d="M19 17v4"/><path d="M17 19h4"/>',
                refresh: '<path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5"/><path d="M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5"/>',
                external: '<path d="M15 3h6v6"/><path d="m10 14 11-11"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
            };
            return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ''}</svg>`;
        };

        const root = document.createElement('div');
        root.id = 'dba-workspace';
        root.innerHTML = `
            <style>
                #dba-workspace, #dba-workspace * { box-sizing: border-box; letter-spacing: 0; }
                #dba-workspace button, #dba-workspace input, #dba-workspace select {
                    appearance: none; -webkit-appearance: none; -moz-appearance: none;
                    margin: 0; outline: none;
                }
                :where(#dba-workspace) button { background-color: transparent; border-style: solid; }
                #dba-workspace { color-scheme: light only; forced-color-adjust: none; }
                #dba-launcher-host, #dba-panel, #dba-preview { color-scheme: light only; forced-color-adjust: none; }
                #dba-launcher-host { position: fixed; right: 18px; bottom: 22px; z-index: 2147483645; }
                #doubao-assistant-btn { position: relative; min-width: 142px; height: 52px; display: flex; align-items: center; gap: 10px; padding: 0 14px 0 8px; border: 1px solid #9bdc63; border-radius: 14px; color: #20261d; background: rgba(255, 253, 247, .96); box-shadow: 0 10px 30px rgba(91, 145, 55, .2); backdrop-filter: blur(10px); cursor: pointer; font: 600 13px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; transition: transform .22s cubic-bezier(.2, .8, .2, 1), box-shadow .22s ease, border-color .18s ease, background-color .18s ease; }
                #doubao-assistant-btn[hidden] { display: none !important; }
                #doubao-assistant-btn:hover { border-color: #7fc746; background: #fffef9; box-shadow: 0 13px 34px rgba(91, 145, 55, .28); transform: translateY(-2px); }
                #doubao-assistant-btn:active { box-shadow: 0 7px 20px rgba(91, 145, 55, .2); transform: translateY(0) scale(.985); transition-duration: .1s; }
                #doubao-assistant-btn svg { width: 21px; height: 21px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
                .dba-launcher-icon { width: 36px; height: 36px; flex: none; display: grid; place-items: center; border-radius: 50%; background: #edf8e4; transition: transform .22s cubic-bezier(.2, .8, .2, 1), background-color .18s ease; }
                #doubao-assistant-btn:hover .dba-launcher-icon { background: #e3f5d4; transform: scale(1.05); }
                .dba-launcher-icon img { width: 25px; height: 25px; display: block; object-fit: contain; }
                .dba-launcher-copy { display: grid; gap: 3px; text-align: left; }
                .dba-launcher-name { white-space: nowrap; }
                .dba-launcher-kind { color: #718268; font-size: 9px; font-weight: 500; white-space: nowrap; }
                #dba-panel { position: fixed; z-index: 2147483644; top: 12px; right: 12px; bottom: 12px; width: min(404px, calc(100vw - 24px)); max-width: 100vw; box-sizing: border-box; display: grid; grid-template-rows: auto auto auto auto minmax(0, 1fr) auto; overflow: hidden; border: 1px solid #d9dce2; border-radius: 8px; color: #202124; background: #fff; box-shadow: 0 18px 56px rgba(20, 24, 31, .22); font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; transform: translateX(calc(100% + 28px)); visibility: hidden; transition: transform .2s ease, visibility .2s; }
                #dba-panel[data-open="true"] { transform: translateX(0); visibility: visible; }
                #dba-panel { isolation: isolate; }
                #dba-workspace button, #dba-workspace input, #dba-workspace label { appearance: none; -webkit-appearance: none; }
                .dba-head { min-height: 58px; display: flex; align-items: center; gap: 12px; padding: 0 14px 0 16px; border-bottom: 1px solid #e6e8ec; }
                .dba-brand { min-width: 0; flex: 1; }
                .dba-title { font-size: 15px; font-weight: 650; color: #17191c; }
                .dba-summary { margin-top: 2px; color: #747982; font-size: 11px; }
                .dba-icon-btn { width: 32px; height: 32px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 6px; color: #646a73; background: transparent; cursor: pointer; }
                .dba-icon-btn:hover { color: #17191c; background: #f0f1f3; }
                .dba-icon-btn.loading svg { animation: dba-spin .7s linear infinite; }
                @keyframes dba-spin { to { transform: rotate(360deg); } }
                .dba-icon-btn svg, .dba-button svg, .dba-tab svg, .dba-prompt svg, .dba-prompt-load svg { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
                .dba-seedance { display: flex; flex-direction: column; gap: 8px; padding: 10px 14px; border-bottom: 1px solid #e6e8ec; background: #fafbfc; }
                .dba-seedance-row1 { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
                .dba-seedance-row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
                .dba-seedance-row2 .dba-prompt, .dba-seedance-row2 .dba-prompt-load { flex: 1 1 0; min-width: 100px; text-align: center; }
                .dba-seedance-label { display: flex; align-items: center; gap: 6px; font-weight: 600; white-space: nowrap; }
                .dba-seedance-label svg { width: 15px; height: 15px; fill: none; stroke: #3266d5; stroke-width: 1.8; }
                .dba-toggle { display: inline-flex; align-items: center; gap: 7px; color: #686e77; font-size: 11px; cursor: pointer; }
                .dba-toggle input { position: absolute; opacity: 0; pointer-events: none; }
                .dba-toggle-track { position: relative; width: 34px; height: 20px; border-radius: 10px; background: #c7cbd2 !important; transition: background .16s ease; }
                .dba-toggle-track::after { content: ""; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; border-radius: 50%; background: #fff !important; box-shadow: 0 1px 3px rgba(0, 0, 0, .25); transition: transform .16s ease; }
                .dba-toggle input:checked + .dba-toggle-track { background: #3266d5 !important; }
                .dba-toggle input:checked + .dba-toggle-track::after { transform: translateX(14px); }
                .dba-toggle input:focus-visible + .dba-toggle-track { outline: 2px solid #9bb8f3; outline-offset: 2px; }
                .dba-prompt { height: 32px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 10px; border: 1px solid #c8d5f2; border-radius: 6px; color: #2857b8 !important; background: #edf3ff !important; font: 600 12px/1 inherit; cursor: pointer; white-space: nowrap; }
                .dba-prompt:disabled { opacity: .55; cursor: default; }
                .dba-prompt-load { height: 32px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 10px; border: 1px solid #d5d9e0; border-radius: 6px; color: #2c3138 !important; background: #f2f3f5 !important; font: 600 12px/1 inherit; cursor: pointer; white-space: nowrap; }
                .dba-prompt-load:disabled { opacity: .55; cursor: default; }
                .dba-prompt-load.dba-prompt-load-active { border-color: #b9d6b9; color: #226b22; background: #eef8ee; }
                .dba-tabs { display: grid; grid-template-columns: 1fr 1fr; padding: 0 14px; border-bottom: 1px solid #e6e8ec; }
                .dba-tab { position: relative; height: 44px; display: flex; align-items: center; justify-content: center; gap: 7px; border: 0; color: #717680 !important; background: transparent !important; font: 600 13px/1 inherit; cursor: pointer; }
                .dba-tab.active { color: #2459c4 !important; }
                .dba-tab.active::after { content: ""; position: absolute; left: 20%; right: 20%; bottom: -1px; height: 2px; background: #3266d5; }
                .dba-tab-count { min-width: 20px; padding: 1px 6px; border-radius: 9px; color: inherit; background: #eef0f3; font-size: 10px; }
                .dba-manual-link { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 14px; border-bottom: 1px solid #e6e8ec; background: #fafbfc; }
                .dba-manual-link input { flex: 1 1 160px; min-width: 0; height: 32px; padding: 0 10px; border: 1px solid #d5d9e0; border-radius: 6px; color: #202124; background: #fff; font: 12px/1 inherit; }
                .dba-manual-link input:focus { outline: 2px solid #9bb8f3; outline-offset: -1px; }
                .dba-manual-link .dba-prompt { flex: none; }
                .dba-list { overflow: auto; padding: 10px 12px 18px; background: #f7f8fa; transition: opacity .16s ease; }
                .dba-list.loading { opacity: .5; cursor: progress; pointer-events: none; }
                .dba-list::-webkit-scrollbar { width: 6px; }
                .dba-list::-webkit-scrollbar-thumb { border-radius: 3px; background: #c9cdd3; }
                .dba-topic-divider { display: flex; align-items: center; gap: 8px; min-height: 34px; margin: 8px 0 7px; padding: 0 4px; color: #353941; }
                .dba-topic-divider:first-child { margin-top: 0; }
                .dba-topic-divider::after { content: ""; height: 1px; flex: 1; background: #dfe2e7; }
                .dba-topic-title { max-width: 260px; overflow: hidden; font-size: 12px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
                .dba-topic-count { flex: none; color: #858a93; font-size: 10px; }
                .dba-topic-select { width: 18px; height: 18px; flex: none; accent-color: #3266d5; cursor: pointer; }
                .dba-item { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 12px; min-height: 102px; margin-bottom: 8px; padding: 9px; border: 1px solid #e0e3e8; border-radius: 7px; background: #fff; }
                .dba-item.selected { border-color: #9cb7ee; background: #f7faff; }
                .dba-preview { position: relative; width: 112px; height: 84px; overflow: hidden; border-radius: 5px; background: #e9ebef; cursor: pointer; }
                .dba-preview img { width: 100%; height: 100%; display: block; object-fit: cover; }
                .dba-preview-placeholder { width: 100%; height: 100%; display: grid; place-items: center; color: #8e949d; }
                .dba-preview-placeholder svg { width: 25px; height: 25px; fill: none; stroke: currentColor; stroke-width: 1.5; }
                .dba-type { position: absolute; left: 5px; bottom: 5px; padding: 2px 5px; border-radius: 3px; color: #fff; background: rgba(18, 20, 24, .72); font-size: 9px; }
                .dba-item-body { min-width: 0; display: flex; flex-direction: column; }
                .dba-item-title { overflow: hidden; color: #292c31; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
                .dba-meta { min-height: 18px; margin-top: 3px; color: #858a93; font-size: 11px; }
                .dba-actions { display: flex; align-items: center; gap: 5px; margin-top: auto; }
                .dba-button { height: 30px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 0 9px; border: 1px solid #d8dbe1; border-radius: 5px; color: #4f555e; background: #fff; font: 500 11px/1 inherit; cursor: pointer; }
                .dba-button:hover { color: #2459c4; border-color: #aabce1; background: #f5f8ff; }
                .dba-button:disabled { opacity: .48; cursor: not-allowed; pointer-events: none; }
                .dba-button.icon-only { width: 30px; padding: 0; }
                .dba-select { width: 18px; height: 18px; margin-left: auto; accent-color: #3266d5; cursor: pointer; }
                .dba-empty { height: 100%; min-height: 260px; display: grid; place-content: center; justify-items: center; color: #858a93; text-align: center; }
                .dba-empty svg { width: 34px; height: 34px; margin-bottom: 10px; fill: none; stroke: #a6abb3; stroke-width: 1.4; }
                .dba-empty strong { color: #555b64; font-size: 13px; }
                .dba-empty span { margin-top: 4px; font-size: 11px; }
                .dba-foot { min-height: 54px; display: flex; flex-wrap: wrap; align-items: center; gap: 7px; padding: 10px 12px; border-top: 1px solid #e3e5e9; background: #fff; }
                .dba-foot-status { min-width: 0; flex: 1 1 140px; color: #797e87; font-size: 11px; }
                .dba-primary { color: #fff; border-color: #3266d5; background: #3266d5; }
                .dba-primary:hover { color: #fff; border-color: #2858b9; background: #2858b9; }
                .dba-operation-loading { position: absolute; z-index: 20; inset: 0; display: grid; place-content: center; justify-items: center; gap: 12px; color: #2857b8; background: rgba(255, 255, 255, .86); backdrop-filter: blur(3px); cursor: progress; }
                .dba-operation-loading[hidden] { display: none; }
                .dba-operation-loading svg { width: 30px; height: 30px; fill: none; stroke: currentColor; stroke-width: 1.8; animation: dba-spin .7s linear infinite; }
                .dba-operation-loading span { font-size: 13px; font-weight: 650; }
                .dba-toast { position: absolute; z-index: 30; top: 70px; left: 50%; max-width: calc(100% - 32px); padding: 9px 14px; border: 1px solid #b9dfa0; border-radius: 8px; color: #33452b; background: rgba(248, 253, 244, .98); box-shadow: 0 8px 24px rgba(51, 69, 43, .16); font-size: 12px; font-weight: 600; white-space: nowrap; opacity: 0; pointer-events: none; transform: translate(-50%, -8px); transition: opacity .18s ease, transform .2s cubic-bezier(.2, .8, .2, 1); }
                .dba-toast.show { opacity: 1; transform: translate(-50%, 0); }
                #dba-preview { position: fixed; z-index: 2147483647; inset: 0; display: none; place-items: center; padding: 24px; background: rgba(10, 12, 16, .88); cursor: zoom-out; }
                #dba-preview.show { display: grid; }
                #dba-preview img { max-width: 100%; max-height: 100%; object-fit: contain; }
                @media (max-width: 700px) {
                    #dba-panel { inset: 0; width: 100%; border: 0; border-radius: 0; }
                    #dba-launcher-host { right: 12px !important; bottom: max(12px, env(safe-area-inset-bottom)) !important; }
                    .dba-seedance-label { display: none; }
                    .dba-prompt, .dba-prompt-load { height: 38px; font-size: 12.5px; }
                    .dba-manual-link input { flex: 1 1 100%; height: 38px; font-size: 13px; }
                    .dba-manual-link .dba-prompt { flex: 1 1 100%; }
                    .dba-tab { height: 48px; }
                    .dba-button { height: 34px; padding: 0 11px; }
                    .dba-foot { padding-bottom: max(10px, env(safe-area-inset-bottom)); }
                    .dba-item { grid-template-columns: 96px minmax(0, 1fr); }
                }
                @media (max-width: 380px) {
                    .dba-foot { gap: 6px; }
                    .dba-button { flex: 1 1 auto; padding: 0 6px; font-size: 10.5px; }
                }
            </style>
            <div id="dba-launcher-host">
                <button id="doubao-assistant-btn" type="button" aria-label="Open KASH DOLA Assistant" title="Open KASH DOLA Assistant">
                    <span class="dba-launcher-icon"><img src="${LAUNCHER_ICON_DATA_URL}" alt=""></span>
                    <span class="dba-launcher-copy"><span class="dba-launcher-name">KASH DOLA Assistant</span></span>
                </button>
            </div>
            <aside id="dba-panel" data-open="false" aria-label="KASH DOLA Assistant Workspace">
                <header class="dba-head">
                    <div class="dba-brand"><div class="dba-title">KASH DOLA Assistant</div><div class="dba-summary">Media Workspace</div></div>
                    <button class="dba-icon-btn" data-action="refresh" title="Refresh media" aria-label="Refresh media">${icon('refresh')}</button>
                    <button class="dba-icon-btn" data-action="close" title="Close" aria-label="Close">${icon('close')}</button>
                </header>
                <section class="dba-seedance">
                    <div class="dba-seedance-row1">
                        <div class="dba-seedance-label">${icon('sparkles')} Seedance</div>
                        <label class="dba-toggle" title="Enable 15s video enhancement">
                            <input type="checkbox" data-action="seedance-toggle" aria-label="Enable Seedance 15s enhancement">
                            <span class="dba-toggle-track"></span><span>15s Enhancement</span>
                        </label>
                    </div>
                    <div class="dba-seedance-row2">
                        <button class="dba-prompt-load" data-action="load-prompt" title="Load a .txt file as the Seedance prompt">${icon('image')} Load Prompt</button>
                        <input type="file" id="dba-prompt-file-input" accept=".txt,text/plain" style="display:none">
                        <button class="dba-prompt" data-action="prompt">Send Prompt</button>
                    </div>
                </section>
                <nav class="dba-tabs" aria-label="Media type">
                    <button class="dba-tab active" data-tab="image">${icon('image')} Images <span class="dba-tab-count" data-count="image">0</span></button>
                    <button class="dba-tab" data-tab="video">${icon('video')} Videos <span class="dba-tab-count" data-count="video">0</span></button>
                </nav>
                <section class="dba-manual-link">
                    <input type="text" id="dba-manual-link-input" placeholder="Paste a video/image link that wasn't auto-fetched" aria-label="Manual media link">
                    <button class="dba-prompt" data-action="add-link">${icon('download')} Add</button>
                </section>
                <main class="dba-list"></main>
                <footer class="dba-foot">
                    <div class="dba-foot-status">No media selected</div>
                    <button class="dba-button" data-action="select-all">Select All</button>
                    <button class="dba-button" data-action="multi-download" disabled>${icon('download')} Batch Download</button>
                    <button class="dba-button dba-primary" data-action="batch" disabled>${icon('archive')} Package Download</button>
                </footer>
                <div class="dba-operation-loading" hidden aria-live="polite" aria-busy="true">${icon('refresh')}<span>Downloading...</span></div>
                <div class="dba-toast" role="status" aria-live="polite"></div>
            </aside>
            <div id="dba-preview"><img alt="Image preview"></div>
        `;
        document.body.appendChild(root);

        const launcher = root.querySelector('#doubao-assistant-btn');
        const panel = root.querySelector('#dba-panel');
        const list = root.querySelector('.dba-list');
        const status = root.querySelector('.dba-foot-status');
        const operationLoading = root.querySelector('.dba-operation-loading');
        const toast = root.querySelector('.dba-toast');
        const preview = root.querySelector('#dba-preview');
        let activeTab = 'image';
        let images = [];
        let videos = [];
        let downloading = false;
        let toastTimer = null;

        const escapeAttr = (value) => String(value ?? '')
            .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
            .replace(/</g, '&lt;').replace(/>/g, '&gt;');

        const getItems = (type = activeTab) => type === 'image' ? images : videos;
        const selectedInputs = () => [...list.querySelectorAll('.dba-select:checked')];
        const showToast = (message) => {
            if (toastTimer) clearTimeout(toastTimer);
            toast.textContent = message;
            toast.classList.add('show');
            toastTimer = setTimeout(() => toast.classList.remove('show'), 1800);
        };
        const updateSelection = (message = '') => {
            const count = selectedInputs().length;
            const multiButton = root.querySelector('[data-action="multi-download"]');
            const batchButton = root.querySelector('[data-action="batch"]');
            multiButton.disabled = downloading || count < 2;
            batchButton.disabled = downloading || count < 2;
            status.textContent = message || (count ? `${count} items selected` : 'No media selected');
            list.querySelectorAll('.dba-item').forEach(item => {
                item.classList.toggle('selected', Boolean(item.querySelector('.dba-select:checked')));
            });
            list.querySelectorAll('.dba-topic-select').forEach(topicInput => {
                const topicInputs = [...list.querySelectorAll(`.dba-item[data-topic-number="${topicInput.dataset.topicNumber}"] .dba-select`)];
                const selectedCount = topicInputs.filter(input => input.checked).length;
                topicInput.checked = topicInputs.length > 0 && selectedCount === topicInputs.length;
                topicInput.indeterminate = selectedCount > 0 && selectedCount < topicInputs.length;
            });
        };

        const render = () => {
            root.querySelectorAll('.dba-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === activeTab));
            const items = getItems();
            if (!items.length) {
                list.innerHTML = `<div class="dba-empty">${icon(activeTab)}<strong>No ${activeTab === 'image' ? 'images' : 'videos'} yet</strong><span>Click the refresh button in the top-right corner to fetch media from the current page</span></div>`;
                updateSelection();
                return;
            }
            const topicOrder = new Map();
            [...images, ...videos].forEach(media => {
                const topicId = media.topicId || 'uncategorized';
                if (!topicOrder.has(topicId)) topicOrder.set(topicId, topicOrder.size);
            });
            const displayItems = items
                .map((media, index) => ({ media, index }))
                .sort((first, second) => topicOrder.get(first.media.topicId || 'uncategorized')
                    - topicOrder.get(second.media.topicId || 'uncategorized'));

            list.innerHTML = displayItems.map(({ media, index }, displayIndex) => {
                const isImage = activeTab === 'image';
                const resolution = media.width && media.height ? `${media.width} × ${media.height}` : 'Unknown size';
                const duration = !isImage && media.duration ? ` · ${Math.floor(media.duration / 60)}:${String(Math.floor(media.duration % 60)).padStart(2, '0')}` : '';
                const previewSource = isImage
                    ? (media.previewUrl || media.url)
                    : (media.poster_url || media.previewUrl || '');
                const previewUrl = escapeAttr(previewSource);
                const topicId = media.topicId || 'uncategorized';
                const topicNumber = (topicOrder.get(topicId) ?? 0) + 1;
                const topicItemNumber = displayItems
                    .slice(0, displayIndex + 1)
                    .filter(entry => (entry.media.topicId || 'uncategorized') === topicId).length;
                media.topicNumber = topicNumber;
                media.topicItemNumber = topicItemNumber;
                const isNewTopic = displayIndex === 0
                    || (displayItems[displayIndex - 1]?.media.topicId || 'uncategorized') !== topicId;
                const topicCount = isNewTopic ? items.filter(item => (item.topicId || 'uncategorized') === topicId).length : 0;
                const topicHeader = isNewTopic
                    ? `<div class="dba-topic-divider"><span class="dba-topic-title">Topic ${topicNumber} · ${escapeAttr(media.topicTitle || 'Uncategorized Topic')}</span><span class="dba-topic-count">${topicCount} items</span><input class="dba-topic-select" type="checkbox" data-topic-number="${topicNumber}" aria-label="Select all media from topic ${topicNumber}"></div>`
                    : '';
                return `${topicHeader}<article class="dba-item" data-index="${index}" data-topic-number="${topicNumber}">
                    <div class="dba-preview" data-action="preview" data-index="${index}">
                        ${previewUrl
                            ? `<img src="${previewUrl}" alt="${isImage ? 'Image' : 'Video cover'} ${index + 1}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
                            : `<div class="dba-preview-placeholder">${icon('video')}</div>`}
                        <span class="dba-type">${isImage ? 'IMAGE' : 'VIDEO'}</span>
                    </div>
                    <div class="dba-item-body">
                        <div class="dba-item-title">Topic ${topicNumber} · ${isImage ? 'Image' : 'Video'} ${topicItemNumber}</div>
                        <div class="dba-meta">${resolution}${duration}</div>
                        <div class="dba-actions">
                            <button class="dba-button" data-action="download" data-index="${index}">${icon('download')} Download</button>
                            <button class="dba-button" data-action="copy" data-index="${index}">${icon('copy')} Copy Link</button>
                            <button class="dba-button icon-only" data-action="open" data-index="${index}" title="Open in new tab" aria-label="Open item ${index + 1} in new tab">${icon('external')}</button>
                            <input class="dba-select" type="checkbox" data-index="${index}" aria-label="Select item ${index + 1}">
                        </div>
                    </div>
                </article>`;
            }).join('');
            updateSelection();
        };

        const notifyButton = (button, label) => {
            const original = button.innerHTML;
            button.innerHTML = `${icon('check')} ${label}`;
            setTimeout(() => { if (button.isConnected) button.innerHTML = original; }, 1400);
        };

        const refreshMedia = async (triggerButton = null) => {
            if (triggerButton?.disabled) return;
            const startedAt = Date.now();
            if (triggerButton) {
                triggerButton.disabled = true;
                triggerButton.classList.add('loading');
                triggerButton.setAttribute('aria-busy', 'true');
            }
            status.textContent = 'Fetching media from current page...';
            list.setAttribute('aria-busy', 'true');
            list.classList.add('loading');
            await new Promise(resolve => requestAnimationFrame(resolve));

            try {
                images = extractImages();
                videos = extractVideos();
                root.querySelector('[data-count="image"]').textContent = images.length;
                root.querySelector('[data-count="video"]').textContent = videos.length;
                if (!getItems(activeTab).length && getItems(activeTab === 'image' ? 'video' : 'image').length) {
                    activeTab = activeTab === 'image' ? 'video' : 'image';
                }
                render();
                const total = images.length + videos.length;
                status.textContent = total
                    ? `Fetched ${images.length} images, ${videos.length} videos`
                    : 'No media found on the current page';
            } catch (error) {
                status.textContent = 'Failed to fetch media, please try again';
            } finally {
                const remainingDelay = 450 - (Date.now() - startedAt);
                if (remainingDelay > 0) {
                    await new Promise(resolve => setTimeout(resolve, remainingDelay));
                }
                list.removeAttribute('aria-busy');
                list.classList.remove('loading');
                if (triggerButton) {
                    triggerButton.disabled = false;
                    triggerButton.classList.remove('loading');
                    triggerButton.removeAttribute('aria-busy');
                }
            }
        };

        const findComposer = () => {
            const candidates = [
                'div[data-slate-editor="true"]',
                '[contenteditable="true"][data-placeholder]',
                'textarea[placeholder]',
                '[contenteditable="true"][role="textbox"]',
                'div[contenteditable="true"]',
                'textarea'
            ];
            return candidates.flatMap(selector => [...document.querySelectorAll(selector)])
                .find(element => isVisible(element)) || null;
        };

        const getComposerText = (composer) => {
            if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
                return composer.value.trim();
            }
            return (composer.innerText || composer.textContent || '').trim();
        };

        const fillComposer = (composer, text) => {
            composer.focus();
            if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
                const prototype = composer instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
                setter ? setter.call(composer, text) : (composer.value = text);
                composer.dispatchEvent(new Event('input', { bubbles: true }));
                composer.dispatchEvent(new Event('change', { bubbles: true }));
                return;
            }

            document.execCommand('selectAll', false, null);
            const inserted = document.execCommand('insertText', false, text);
            if (!inserted) composer.textContent = text;
            composer.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                data: text,
                inputType: 'insertText'
            }));
        };

        const findSendButton = (composer) => {
            const explicitSelectors = [
                'button[type="submit"]',
                'button[aria-label*="发送"]',
                'button[title*="发送"]',
                'button[aria-label*="send" i]',
                'button[title*="send" i]',
                'button[data-testid*="send" i]'
            ];
            for (const selector of explicitSelectors) {
                const candidate = [...document.querySelectorAll(selector)].find(element =>
                    element instanceof HTMLButtonElement
                    && !element.disabled
                    && !element.closest('#dba-workspace')
                    && isVisible(element)
                );
                if (candidate) return candidate;
            }

            const composerRect = composer.getBoundingClientRect();
            let container = composer.parentElement;
            for (let depth = 0; container && depth < 6; depth++, container = container.parentElement) {
                const candidates = [...container.querySelectorAll('button')].filter(element => {
                    if (!(element instanceof HTMLButtonElement) || element.disabled || !isVisible(element)) return false;
                    if (element.closest('#dba-workspace')) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.left >= composerRect.left + composerRect.width * 0.55
                        && Math.abs(rect.bottom - composerRect.bottom) < 120;
                });
                if (candidates.length) {
                    return candidates.sort((first, second) =>
                        second.getBoundingClientRect().right - first.getBoundingClientRect().right
                    )[0];
                }
            }
            return null;
        };

        const waitForComposerClear = async (composer, timeout = 1600) => {
            const startedAt = Date.now();
            while (Date.now() - startedAt < timeout) {
                if (!getComposerText(composer).includes(activeSeedancePromptMarker)) return true;
                await new Promise(resolve => setTimeout(resolve, 80));
            }
            return false;
        };

        const dispatchEnter = (composer) => {
            const eventInit = {
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                bubbles: true,
                cancelable: true
            };
            composer.dispatchEvent(new KeyboardEvent('keydown', eventInit));
            composer.dispatchEvent(new KeyboardEvent('keypress', eventInit));
            composer.dispatchEvent(new KeyboardEvent('keyup', eventInit));
        };

        const sendPrompt = async (button) => {
            const composer = findComposer();
            if (!composer) throw new Error('Input field not found');
            button.disabled = true;
            button.textContent = 'Sending...';
            fillComposer(composer, activeSeedancePrompt);
            await new Promise(resolve => setTimeout(resolve, 250));

            const sendButton = findSendButton(composer);
            if (sendButton) sendButton.click();
            if (!sendButton || !await waitForComposerClear(composer)) {
                composer.focus();
                dispatchEnter(composer);
                if (!await waitForComposerClear(composer, 1000)) {
                    throw new Error('Prompt not submitted');
                }
            }
            button.textContent = 'Sent';
            setTimeout(() => { button.disabled = false; button.textContent = 'Send Prompt'; }, 1300);
        };

        const beginDownloadOperation = (message) => {
            if (downloading) return false;
            downloading = true;
            operationLoading.querySelector('span').textContent = message;
            operationLoading.hidden = false;
            panel.setAttribute('aria-busy', 'true');
            updateSelection(message);
            return true;
        };

        const finishDownloadOperation = (message) => {
            downloading = false;
            operationLoading.hidden = true;
            panel.removeAttribute('aria-busy');
            updateSelection(message);
        };

        const getSelectedIndexes = () => selectedInputs().map(input => Number(input.dataset.index));

        const runMultiDownload = async () => {
            const indexes = getSelectedIndexes();
            if (indexes.length < 2 || !beginDownloadOperation('Preparing batch download...')) return;
            const operationType = activeTab;
            const operationItems = getItems(operationType);

            let completed = 0;
            let resultMessage = '';
            try {
                for (let position = 0; position < indexes.length; position++) {
                    const index = indexes[position];
                    const media = operationItems[index];
                    if (!media) continue;
                    operationLoading.querySelector('span').textContent = `Downloading ${position + 1}/${indexes.length}`;
                    const task = createDownloadTask(media.url, getDownloadFilename(
                        operationType,
                        index,
                        media.url,
                        media.topicNumber,
                        media.topicItemNumber
                    ));
                    await task.promise;
                    completed++;
                }
            } catch (error) {
                resultMessage = 'Batch download failed, please try again';
            }

            finishDownloadOperation(resultMessage || `Downloaded ${completed} items`);
        };

        const runBatch = async () => {
            const indexes = getSelectedIndexes();
            if (indexes.length < 2 || !beginDownloadOperation('Preparing package...')) return;
            const operationType = activeTab;
            const operationItems = getItems(operationType);

            const archiveEntries = [];
            let resultMessage = '';
            try {
                for (let position = 0; position < indexes.length; position++) {
                    const index = indexes[position];
                    const media = operationItems[index];
                    if (!media) continue;
                    operationLoading.querySelector('span').textContent = `Reading ${position + 1}/${indexes.length}`;
                    const blob = await fetchMediaBlob(media.url);
                    archiveEntries.push({
                        name: getDownloadFilename(
                            operationType,
                            index,
                            media.url,
                            media.topicNumber,
                            media.topicItemNumber
                        ),
                        data: new Uint8Array(await blob.arrayBuffer())
                    });
                }

                if (archiveEntries.length) {
                    operationLoading.querySelector('span').textContent = 'Generating ZIP package...';
                    await new Promise(resolve => requestAnimationFrame(resolve));
                    const zipBlob = createZipArchive(archiveEntries);
                    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
                    downloadBlob(zipBlob, `doubao_${operationType}_${timestamp}.zip`);
                    resultMessage = `Packaged ${archiveEntries.length} items`;
                }
            } catch (error) {
                resultMessage = 'Package failed, please try again';
            }

            finishDownloadOperation(resultMessage || `Packaged ${archiveEntries.length} items`);
        };

        const closeWorkspace = () => {
            panel.dataset.open = 'false';
            launcher.hidden = false;
            preview.classList.remove('show');
        };

        launcher.addEventListener('click', async () => {
            panel.dataset.open = 'true';
            launcher.hidden = true;
            await refreshMedia(root.querySelector('[data-action="refresh"]'));
        });
        preview.addEventListener('click', () => preview.classList.remove('show'));
        document.addEventListener('pointerdown', event => {
            if (panel.dataset.open !== 'true') return;
            const target = event.target;
            if (!(target instanceof Node)) return;
            if (panel.contains(target) || launcher.contains(target) || preview.contains(target)) return;
            closeWorkspace();
        });
        const promptFileInput = root.querySelector('#dba-prompt-file-input');
        const manualLinkInput = root.querySelector('#dba-manual-link-input');

        const addManualMediaLink = () => {
            if (!manualLinkInput) return;
            const rawUrl = manualLinkInput.value.trim();
            if (!rawUrl) {
                status.textContent = 'Please paste a link first';
                return;
            }
            let parsedUrl;
            try {
                parsedUrl = new pageWindow.URL(rawUrl);
            } catch (error) {
                status.textContent = 'That does not look like a valid link';
                return;
            }
            if (!/^https?:$/.test(parsedUrl.protocol)) {
                status.textContent = 'Only http/https links are supported';
                return;
            }

            const isImage = /\.(jpe?g|png|gif|webp|bmp|avif)(\?|#|$)/i.test(parsedUrl.pathname);
            const manualTopic = { id: 'manual-links', title: 'Manually Added' };

            if (isImage) {
                addChatImage({ url: rawUrl, width: 0, height: 0, topic: manualTopic });
                images = extractImages();
                root.querySelector('[data-count="image"]').textContent = images.length;
                activeTab = 'image';
            } else {
                addChatVideo({ url: rawUrl, width: 0, height: 0 }, manualTopic);
                videos = extractVideos();
                root.querySelector('[data-count="video"]').textContent = videos.length;
                activeTab = 'video';
            }

            render();
            manualLinkInput.value = '';
            status.textContent = `Link added to ${isImage ? 'Images' : 'Videos'}`;
        };

        if (manualLinkInput) {
            manualLinkInput.addEventListener('keydown', event => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    addManualMediaLink();
                }
            });
        }
        const loadPromptButton = root.querySelector('[data-action="load-prompt"]');
        const markLoadPromptActive = (fileName) => {
            if (!loadPromptButton) return;
            loadPromptButton.classList.add('dba-prompt-load-active');
            loadPromptButton.title = fileName ? `Custom prompt loaded: ${fileName}` : 'Custom prompt loaded';
        };
        if (loadPromptButton && activeSeedancePrompt !== SEEDANCE_SYSTEM_PROMPT) markLoadPromptActive();
        if (promptFileInput) {
            promptFileInput.addEventListener('change', () => {
                const file = promptFileInput.files && promptFileInput.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => {
                    const text = typeof reader.result === 'string' ? reader.result : '';
                    if (setActiveSeedancePrompt(text)) {
                        saveCustomPromptToStorage(activeSeedancePrompt);
                        markLoadPromptActive(file.name);
                        status.textContent = `Loaded prompt from ${file.name}`;
                    } else {
                        status.textContent = 'Selected file is empty';
                    }
                    promptFileInput.value = '';
                };
                reader.onerror = () => {
                    status.textContent = 'Could not read the selected file';
                    promptFileInput.value = '';
                };
                reader.readAsText(file);
            });
        }
        list.addEventListener('change', event => {
            if (event.target.matches('.dba-topic-select')) {
                const topicNumber = event.target.dataset.topicNumber;
                list.querySelectorAll(`.dba-item[data-topic-number="${topicNumber}"] .dba-select`)
                    .forEach(input => { input.checked = event.target.checked; });
                updateSelection();
                return;
            }
            if (event.target.matches('.dba-select')) updateSelection();
        });
        root.addEventListener('click', async event => {
            const target = event.target.closest('[data-action], [data-tab]');
            if (!target) return;
            if (target.dataset.tab) {
                activeTab = target.dataset.tab;
                render();
                return;
            }
            const action = target.dataset.action;
            if (action === 'close') {
                closeWorkspace();
            }
            if (action === 'refresh') await refreshMedia(target);
            if (action === 'seedance-toggle') {
                setSeedanceDurationConfig(15, target.checked);
                if (target.checked) inject15sDurationOption();
                status.textContent = target.checked ? 'Seedance 15s enhancement enabled' : '15s enhancement disabled, using official duration';
                return;
            }
            if (action === 'prompt') {
                try {
                    await sendPrompt(target);
                } catch (error) {
                    target.disabled = false;
                    target.textContent = 'Send Prompt';
                    status.textContent = `Send failed: ${error?.message || 'Send button not found'}`;
                }
            }
            if (action === 'load-prompt') {
                const fileInput = root.querySelector('#dba-prompt-file-input');
                if (fileInput) fileInput.click();
            }
            if (action === 'add-link') addManualMediaLink();
            if (action === 'select-all') {
                const inputs = [...list.querySelectorAll('.dba-select')];
                const shouldSelect = inputs.some(input => !input.checked);
                inputs.forEach(input => { input.checked = shouldSelect; });
                target.textContent = shouldSelect ? 'Deselect All' : 'Select All';
                updateSelection();
            }
            if (action === 'multi-download') await runMultiDownload();
            if (action === 'batch') await runBatch();
            if (action === 'preview') {
                const media = getItems()[Number(target.dataset.index)];
                if (!media) return;
                const previewUrl = activeTab === 'image'
                    ? (media.previewUrl || media.url)
                    : (media.poster_url || media.previewUrl || '');
                if (previewUrl) {
                    preview.querySelector('img').src = previewUrl;
                    preview.classList.add('show');
                } else {
                    status.textContent = 'This video has no available cover, open in new tab instead';
                }
            }
            if (action === 'download') {
                const index = Number(target.dataset.index);
                const media = getItems()[index];
                if (media) {
                    const message = `Started downloading ${activeTab === 'image' ? 'image' : 'video'} ${media.topicItemNumber || index + 1}`;
                    status.textContent = message;
                    showToast(message);
                    await downloadImage(media.url, getDownloadFilename(
                        activeTab,
                        index,
                        media.url,
                        media.topicNumber,
                        media.topicItemNumber
                    ));
                }
            }
            if (action === 'copy') {
                const media = getItems()[Number(target.dataset.index)];
                if (!media) return;
                try {
                    await copyTextToClipboard(media.url);
                    notifyButton(target, 'Copied');
                } catch (error) { status.textContent = 'Copy failed, please check browser permissions'; }
            }
            if (action === 'open') {
                const media = getItems()[Number(target.dataset.index)];
                if (!media) return;
                if (typeof GM_openInTab === 'function') {
                    GM_openInTab(media.url, { active: true, insert: true, setParent: true });
                } else {
                    const openedWindow = pageWindow.open(media.url, '_blank', 'noopener,noreferrer');
                    if (!openedWindow) status.textContent = 'New tab was blocked by the browser';
                }
            }
        });

        loadSeedanceDurationConfig();
        root.querySelector('[data-action="seedance-toggle"]').checked = seedanceDurationEnabled;
        uiInitialized = true;
    }

    function initScript() {
        if (isDoubaoPage()) {
            startDurationMenuObserver();
            if (seedanceTargetDuration === 15) {
                patchDurationTriggerLabels();
            }
        }

        if (pageWindow.location.pathname.includes('/chat/')) {
            createAssistantWorkspace();
            return;
        }

        const hasScriptData = !!document.querySelector(
            'script[data-script-src="modern-run-router-data-fn"], script[data-script-src="modern-run-window-fn"][data-fn-name="mergeLoaderData"]'
        );
        const hasRouterData = !!window._ROUTER_DATA;

        if (!hasScriptData && !hasRouterData) {
            initRetryCount++;
            if (initRetryCount < MAX_RETRY) {
                setTimeout(initScript, 500);
                return;
            }
        }

        if (isDoubaoPage() && pageWindow.location.pathname.includes('/thread/')) {
            replaceChatImages(extractSharePageImages());
        }

        createAssistantWorkspace();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initScript);
    } else if (document.readyState === 'interactive') {
        if (document.body) {
            initScript();
        } else {
            document.addEventListener('DOMContentLoaded', initScript);
        }
    } else {
        initScript();
    }

})();