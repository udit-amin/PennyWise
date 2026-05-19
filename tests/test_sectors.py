from pennywise.analytics.sectors import canonicalize_sector


def test_banks_map_to_financial_services():
    assert canonicalize_sector("Banks") == "Financial Services"
    assert canonicalize_sector("Private Sector Bank") == "Financial Services"
    assert canonicalize_sector("NBFC") == "Financial Services"


def test_software_maps_to_it():
    assert canonicalize_sector("IT - Software") == "Information Technology"
    assert canonicalize_sector("Computers - Software") == "Information Technology"


def test_pharma_maps_to_healthcare():
    assert canonicalize_sector("Pharmaceuticals") == "Healthcare"
    assert canonicalize_sector("Drug Discovery") == "Healthcare"


def test_oil_and_coal_map_to_energy():
    assert canonicalize_sector("Refineries") == "Energy"
    assert canonicalize_sector("Coal") == "Energy"
    assert canonicalize_sector("Power Generation") == "Energy"


def test_etf_and_gold_map_to_etf_bucket():
    assert canonicalize_sector("Gold ETF") == "ETF / Index"
    assert canonicalize_sector("Nifty Index") == "ETF / Index"
    assert canonicalize_sector("Silver Trust") == "ETF / Index"


def test_gics_top_level_labels():
    """Screener now exposes GICS top-level labels under <a title='Broad Sector'>;
    canonicalize_sector must accept them verbatim."""
    assert canonicalize_sector("Energy") == "Energy"
    assert canonicalize_sector("Financials") == "Financial Services"
    assert canonicalize_sector("Information Technology") == "Information Technology"
    assert canonicalize_sector("Health Care") == "Healthcare"
    assert canonicalize_sector("Consumer Staples") == "Consumer Goods"
    assert canonicalize_sector("Consumer Discretionary") == "Consumer Goods"
    assert canonicalize_sector("Industrials") == "Industrials"
    assert canonicalize_sector("Materials") == "Materials"
    assert canonicalize_sector("Utilities") == "Utilities"
    assert canonicalize_sector("Real Estate") == "Real Estate"
    assert canonicalize_sector("Communication Services") == "Communication Services"


def test_unknown_returns_unknown():
    assert canonicalize_sector(None) == "unknown"
    assert canonicalize_sector("") == "unknown"
    assert canonicalize_sector("Some weird new industry") == "unknown"
