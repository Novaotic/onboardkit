def validate_presets(presets: dict, config: dict) -> list[str]:
    errors = []

    # Build allowed sets
    valid_hardware = {item["id"] for item in config["option_groups"]["hardware"]}
    valid_software = {item["id"] for item in config["option_groups"]["software"]}
    valid_portals = {item["id"] for item in config["option_groups"]["portals"]}
    valid_mailboxes = {item["id"] for item in config["option_groups"]["mailboxes"]}
    valid_offices = set(config["offices"])

    for role, preset_data in presets.items():
        for hw_id in preset_data.get("hardware", []):
            if hw_id not in valid_hardware:
                errors.append(f"{role}: Unknown hardware ID '{hw_id}'")
        for sw_id in preset_data.get("software", []):
            if sw_id not in valid_software:
                errors.append(f"{role}: Unknown software ID '{sw_id}'")
        for pt_id in preset_data.get("portals", []):
            if pt_id not in valid_portals:
                errors.append(f"{role}: Unknown portal ID '{pt_id}'")
        for mb_id in preset_data.get("mailboxes", []):
            if mb_id not in valid_mailboxes:
                errors.append(f"{role}: Unknown mailbox ID '{mb_id}'")
        for office in preset_data.get("location_email_groups", {}).keys():
            if office not in valid_offices:
                errors.append(f"{role}: Unknown office '{office}' in location_email_groups")
    return errors