import tldextract

extract = tldextract.TLDExtract(suffix_list_urls=())


def base_domain(hostname):
    result = extract(hostname)
    return result.top_domain_under_public_suffix


def instructions(hostname, records):
    base = base_domain(hostname)
    rows = []
    for record in records:
        name = record["name"].rstrip(".")
        relative = "@" if name == base else name.removesuffix("." + base)
        rows.append(
            {
                "host": relative,
                "type": record["type"],
                "data": record["value"],
                "ttl": "Default",
            }
        )
    return {
        "provider": "squarespace",
        "domain": base,
        "steps": [
            f"Open Squarespace → Domains → {base} → DNS → DNS settings → Custom records → Add record.",
            "Add the exact rows below. Replace conflicting A/AAAA/CNAME records only at these hostnames after reviewing the conflict report. Preserve MX, TXT and unrelated records.",
            "Save, then run publisher-admin domains status with this hostname until DNS and HTTPS verification pass.",
        ],
        "records": rows,
        "help": "https://support.squarespace.com/hc/en-us/articles/360002101888-Edit-your-domain-s-DNS-records",
    }
