# -*- coding: utf-8 -*-
"""
검색 쿼리 파싱용 통합 필드 스키마.
- aaa1_tags: ODD static 속성 (aaa1_prompt_v1.txt 기준, 19개 target)
- lsd_tags: 야간 광원 속성 (lsd_prompt_v1.2.txt 기준, 11개 target)
- motional_tags: Motional 시나리오 58개 (present만 존재하는 이벤트성 라벨)

주의: 여기 정의는 "태깅용" 정의를 검색/필터용으로 재사용한 것.
실제 인덱스 필드명과 다르면 이후 ES 매핑에 맞게 alias 매핑 레이어를 하나 더 둬야 함.
"""

AAA1_TARGETS = [
    "contaminated_lane",
    "invisible_lanes_city_3_5_lanes_many_vehicles_ahead",
    "no_lane_mark_road",
    "two_three_wheelers_carrying_loads",
    "vehicles_carrying_loads_or_protruding_cargo",
    "bullock_carts",
    "hand_pulled_carts",
    "animal_sitting_or_standing_cow",
    "animal_large_dog",
    "barricades",
    "crowded_pedestrian",
    "yellow_line_near_curb",
    "yellow_line_low_sun",
    "white_line_low_sun",
    "road_boundary_low_sun",
    "road_boundary_guardrail_or_wall",
    "road_boundary_lane_separator",
    "road_boundary_flat",
    "road_boundary_parked_car",
]

AAA1_CONTEXT = {
    "time": ["day", "dawn_evening", "night"],
    "da_road_type": [
        "public_road_highway",
        "public_road_city",
        "public_road_rural",
        "parking_lot_outdoor",
        "parking_lot_indoor",
    ],
    "weather": ["clear", "rainy", "heavy_rainy", "snow", "fog"],
}

LSD_TARGETS = [
    "low_streetlight_road",
    "oncoming_vehicle_far_60m",
    "oncoming_vehicle_taillight_cluster",
    "motorbike_headlamp",
    "reflector_guardrail",
    "reflector_lane_divider",
    "reflector_tunnel_lowmid_light",
    "reflector_white_all",
    "sign_strong_light_reflection",
    "sign_white_heavy_reflection",
    "camera_torn_frame",
]

MOTIONAL_TARGETS = [
    "behind_pedestrian_on_driveable",
    "near_barrier_on_driveable",
    "waiting_for_pedestrian_to_cross",
    "starting_straight_traffic_light_intersection_traversal",
    "starting_u_turn",
    "traversing_intersection",
    "traversing_traffic_light_intersection",
    "on_carpark",
    "on_intersection",
    "on_traffic_light_intersection",
    "accelerating_at_traffic_light",
    "stationary_in_traffic",
    "crossed_by_bike",
    "crossed_by_vehicle",
    "crossed_by_motorcycle",
    "on_stopline_traffic_light",
    "near_multiple_bikes",
    "near_multiple_vehicles",
    "near_multiple_motorcycle",
    "accelerating_at_crosswalk",
    "stationary_at_crosswalk",
    "stopping_at_crosswalk",
    "high_lateral_acceleration",
    "near_high_speed_vehicle",
    "near_long_vehicle",
    "near_multiple_pedestrians",
    "near_pedestrian_on_crosswalk",
    "near_pedestrian_on_crosswalk_with_ego",
    "starting_high_speed_turn",
    "starting_low_speed_turn",
    "traversing_crosswalk",
    "on_stopline_crosswalk",
    "high_magnitude_jerk",
    "stationary",
    "high_magnitude_speed",
    "low_magnitude_speed",
    "medium_magnitude_speed",
    "starting_left_turn",
    "starting_right_turn",
    "changing_lane",
    "changing_lane_to_left",
    "changing_lane_to_right",
    "accelerating_at_traffic_light_with_lead",
    "accelerating_at_traffic_light_without_lead",
    "following_lane_with_lead",
    "following_lane_with_slow_lead",
    "following_lane_without_lead",
    "stationary_at_traffic_light_with_lead",
    "stationary_at_traffic_light_without_lead",
    "stopping_at_traffic_light_with_lead",
    "stopping_at_traffic_light_without_lead",
    "stopping_with_lead",
    "stopping_without_lead",
    "behind_bike",
    "behind_long_vehicle",
    "behind_motorcycle",
    "changing_lane_with_lead",
    "changing_lane_with_trail",
]


def build_schema_prompt() -> str:
    """LLM 시스템 프롬프트에 넣을 필드 목록 텍스트를 생성."""
    lines = []
    lines.append("### aaa1_tags (ODD static attributes, value: present|absent|unknown)")
    lines.append(", ".join(AAA1_TARGETS))
    lines.append("")
    lines.append("### aaa1_context (value must be one of the listed options, no 'unknown')")
    for k, vals in AAA1_CONTEXT.items():
        lines.append(f"- {k}: {', '.join(vals)}")
    lines.append("")
    lines.append("### lsd_tags (night light-source attributes, value: present|absent|unknown)")
    lines.append(", ".join(LSD_TARGETS))
    lines.append("")
    lines.append("### motional_tags (scenario/event labels, value: present|absent)")
    lines.append(", ".join(MOTIONAL_TARGETS))
    return "\n".join(lines)