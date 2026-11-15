"""Observable production checks shared by Worker and the local CLI."""

from app.skill_schema import STAGES


def check_production(apr, stage, shot_ids=()):
    selected = set(map(str, shot_ids))
    shots = [
        shot
        for shot in apr["shots"]
        if shot["status"] != "archived" and (not selected or shot["id"] in selected)
    ]
    findings = []
    total = 0

    def record(code, passed, message, entity_id=None, evidence=None):
        nonlocal total
        total += 1
        if len(findings) < 200:
            findings.append(
                {
                    "code": code,
                    "status": "pass" if passed else "warning",
                    "message": message,
                    "entity_id": entity_id,
                    "evidence": evidence,
                }
            )

    if stage == "story":
        record(
            "STORY_BRIEF",
            bool(apr["brief"].strip()),
            "创作简述已填写" if apr["brief"].strip() else "补充创作目标与故事简述",
        )
        record("STORY_SCENES", bool(apr["scenes"]), "工程场景数量", evidence=len(apr["scenes"]))
        record("STORY_SHOTS", bool(shots), "当前检查范围中的镜头数量", evidence=len(shots))
    elif stage == "continuity":
        record(
            "CONTINUITY_CHARACTERS", True, "已登记角色数量", evidence=len(apr.get("characters", []))
        )
        record(
            "CONTINUITY_STATES",
            True,
            "已登记世界状态数量",
            evidence=len(apr.get("world_states", [])),
        )
        record(
            "CONTINUITY_LINKS",
            True,
            "已登记连续性关联数量",
            evidence=len(apr.get("continuity_links", [])),
        )
    elif stage == "shots":
        record("SHOT_COUNT", bool(shots), "检查范围中的活动镜头数量", evidence=len(shots))
        for shot in shots:
            record(
                "SHOT_ACTION",
                bool(shot["action"].strip()),
                "镜头动作描述",
                shot["id"],
                shot["action"][:300],
            )
            record(
                "SHOT_CAMERA",
                bool(shot["camera"]["movement"].strip()),
                "运镜与景别描述",
                shot["id"],
                shot["camera"],
            )
    elif stage == "image":
        images = [asset for asset in apr["assets"] if asset["media_type"] == "image"]
        record("IMAGE_ASSETS", True, "工程图像素材数量", evidence=len(images))
        record(
            "IMAGE_BINDINGS",
            True,
            "镜头参考图、首帧图和尾帧图数量",
            evidence=sum(len(shot.get("reference_images", {})) for shot in shots),
        )
    elif stage == "video":
        versions = {value["id"]: value for value in apr["asset_versions"]}
        record("VIDEO_SHOT_COUNT", bool(shots), "检查范围中的镜头数量", evidence=len(shots))
        for shot in shots:
            version = versions.get(shot["active_asset_version_id"])
            ready = (
                version is not None
                and version["status"] == "ready"
                and shot["status"] in {"ready", "approved"}
            )
            record(
                "VIDEO_SELECTED_READY",
                ready,
                "当前选中视频素材就绪" if ready else "生成或审核当前镜头素材",
                shot["id"],
            )
    elif stage == "audio":
        tracks = [value for value in apr["timeline"]["tracks"] if value["type"] == "audio"]
        record("AUDIO_TRACKS", True, "已编排音轨数量", evidence=len(tracks))
        record(
            "AUDIO_CLIPS",
            True,
            "已编排音频片段数量",
            evidence=sum(len(value["items"]) for value in tracks),
        )
    elif stage == "editing":
        items = apr["timeline"]["tracks"][0]["items"]
        record("EDIT_MAIN_TRACK", bool(items), "视频主轨已编排" if items else "将镜头加入主视频轨")
        record("EDIT_CAPTIONS", True, "字幕数量", evidence=len(apr["timeline"].get("captions", [])))
        record(
            "EDIT_MARKERS", True, "时间标记数量", evidence=len(apr["timeline"].get("markers", []))
        )
    elif stage == "review":
        for shot in shots:
            record(
                "REVIEW_APPROVED",
                shot["status"] == "approved",
                "镜头已批准" if shot["status"] == "approved" else "当前镜头需要人工审阅确认",
                shot["id"],
            )
        record(
            "REVIEW_SCOPE", True, "本次检查覆盖工程批准状态，画面与声音质量请结合审片证据及试听确认"
        )
    elif stage == "delivery":
        record(
            "DELIVERY_RENDER",
            apr.get("render_status") == "ready" and bool(apr.get("render_asset_id")),
            "当前修订成片已就绪" if apr.get("render_status") == "ready" else "渲染当前修订的成片",
        )
        record("DELIVERY_FORMAT", True, "工程成片参数", evidence=apr["format"])
        mock = sum(
            bool(value["provenance"].get("is_mock") or value["provenance"].get("contains_mock"))
            for value in apr["assets"]
        )
        record("DELIVERY_MOCK", True, "含演示标识的工程素材数量", evidence=mock)
    else:
        raise ValueError("Unknown production stage")
    return {
        "kind": "check",
        "stage": stage,
        "stage_name": STAGES[stage],
        "source_revision": apr["revision"],
        "checks": findings,
        "total_checks": total,
        "truncated": total > len(findings),
        "checked_shots": len(shots),
    }
