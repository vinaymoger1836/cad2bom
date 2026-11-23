-- P1 schema: the versioned library (docs/06). Every row is keyed by the library version, so a job's
-- pinned version stays readable after newer versions are loaded. Loaded by `c2b-library load`.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE library_version (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,            -- SHA-256 over all YAML files (LF-normalised)
    released    date NOT NULL,
    changelog   jsonb NOT NULL,
    file_count  integer NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE library_class (
    version                       text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    code                          text NOT NULL,
    parent                        text,
    noun                          text NOT NULL,
    modifiers                     jsonb NOT NULL,
    uom                           text NOT NULL,
    make_buy_default              text NOT NULL,
    usually_modelled              boolean NOT NULL,
    buy_as_assembly               boolean NOT NULL,
    description_template          text NOT NULL,
    standard_families             jsonb NOT NULL,
    material_default_profile_key  text,
    unspsc                        text,
    note                          text,
    PRIMARY KEY (version, code)
);
CREATE INDEX library_class_trgm ON library_class USING gin ((code || ' ' || noun) gin_trgm_ops);

CREATE TABLE library_material (
    version         text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    code            text NOT NULL,
    family          text NOT NULL,
    display         text NOT NULL,
    density_g_cm3   double precision NOT NULL,
    designations    jsonb NOT NULL,
    body            jsonb NOT NULL,       -- the full validated record (uses, finishes, source, verify)
    PRIMARY KEY (version, code)
);

CREATE TABLE library_standard (
    version   text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    key       text NOT NULL,
    standard  text NOT NULL,
    class     text NOT NULL,
    verify    boolean NOT NULL,
    body      jsonb NOT NULL,             -- the whole table: tolerances, lengths, sizes, source
    PRIMARY KEY (version, key)
);

CREATE TABLE library_synonym (
    version     text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    token       text NOT NULL,            -- normalised (NFKC, case-folded)
    class       text NOT NULL,
    lang        text NOT NULL,
    weight      double precision NOT NULL,
    kind        text NOT NULL,
    context     jsonb,
    file        text NOT NULL
);
CREATE INDEX library_synonym_ver_tok ON library_synonym (version, token);
CREATE INDEX library_synonym_trgm ON library_synonym USING gin (token gin_trgm_ops);

CREATE TABLE library_rule (
    version  text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    id       text NOT NULL,
    stage    text NOT NULL,
    weight   double precision NOT NULL,
    note     text NOT NULL,
    body     jsonb NOT NULL,              -- when / then, interpreted by the rules engine, never executed
    PRIMARY KEY (version, id)
);

-- headers, units, profiles, confidence, archetypes, synonym patterns and vendor prefixes.
CREATE TABLE library_blob (
    version  text NOT NULL REFERENCES library_version (version) ON DELETE CASCADE,
    kind     text NOT NULL,
    name     text NOT NULL,
    body     jsonb NOT NULL,
    PRIMARY KEY (version, kind, name)
);
