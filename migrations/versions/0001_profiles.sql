-- Frozen P5-IMP-04 initial profile schema. Do not regenerate after release.


CREATE TABLE business_categories (
	key VARCHAR(16) NOT NULL, 
	label_bg VARCHAR(64) NOT NULL, 
	display_order INTEGER NOT NULL, 
	CONSTRAINT pk_business_categories PRIMARY KEY (key), 
	CONSTRAINT uq_business_categories_label_bg UNIQUE (label_bg), 
	CONSTRAINT uq_business_categories_display_order UNIQUE (display_order)
)

;


CREATE TABLE cpv_catalogue_versions (
	id VARCHAR(64) NOT NULL, 
	official_release VARCHAR(64) NOT NULL, 
	source_url TEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	loaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_cpv_catalogue_versions PRIMARY KEY (id), 
	CONSTRAINT ck_cpv_catalogue_versions_provenance CHECK (sha256 ~ '^[0-9a-f]{64}$' AND status IN ('STAGED','ACTIVE'))
)

;

CREATE UNIQUE INDEX uq_cpv_single_active_catalogue ON cpv_catalogue_versions (status) WHERE status = 'ACTIVE';


CREATE TABLE oblasts (
	code VARCHAR(16) NOT NULL, 
	name_bg VARCHAR(64) NOT NULL, 
	display_order INTEGER NOT NULL, 
	CONSTRAINT pk_oblasts PRIMARY KEY (code), 
	CONSTRAINT uq_oblasts_name_bg UNIQUE (name_bg), 
	CONSTRAINT uq_oblasts_display_order UNIQUE (display_order)
)

;


CREATE TABLE telegram_users (
	id UUID NOT NULL, 
	scope VARCHAR(64) NOT NULL, 
	telegram_user_id BIGINT NOT NULL, 
	private_chat_id BIGINT NOT NULL, 
	status VARCHAR(16) DEFAULT 'ACTIVE' NOT NULL, 
	revocation_generation INTEGER DEFAULT '1' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_telegram_users PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_users_scope UNIQUE (scope, telegram_user_id), 
	CONSTRAINT ck_telegram_users_private_identity CHECK (telegram_user_id > 0 AND private_chat_id = telegram_user_id), 
	CONSTRAINT ck_telegram_users_status CHECK (status IN ('ACTIVE','REVOKED') AND revocation_generation > 0)
)

;


CREATE TABLE companies (
	id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	name VARCHAR(120), 
	report_email VARCHAR(254), 
	profile_revision INTEGER DEFAULT '1' NOT NULL, 
	category_revision INTEGER DEFAULT '1' NOT NULL, 
	classification_status VARCHAR(20) DEFAULT 'PENDING_CPV' NOT NULL, 
	revocation_generation INTEGER DEFAULT '1' NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_companies PRIMARY KEY (id), 
	CONSTRAINT uq_companies_id UNIQUE (id, owner_user_id), 
	CONSTRAINT ck_companies_revisions CHECK (profile_revision > 0 AND category_revision > 0 AND revocation_generation > 0), 
	CONSTRAINT ck_companies_classification CHECK (classification_status IN ('PENDING_CPV','READY','FAILED','REVOKED')), 
	CONSTRAINT ck_companies_complete_or_purged CHECK ((deleted_at IS NULL AND name IS NOT NULL AND length(trim(name)) > 0 AND report_email IS NOT NULL AND length(trim(report_email)) > 0 AND classification_status <> 'REVOKED') OR (deleted_at IS NOT NULL AND name IS NULL AND report_email IS NULL AND classification_status = 'REVOKED')), 
	CONSTRAINT fk_companies_owner_user_id_telegram_users FOREIGN KEY(owner_user_id) REFERENCES telegram_users (id)
)

;

CREATE INDEX ix_companies_owner_live ON companies (owner_user_id) WHERE deleted_at IS NULL;


CREATE TABLE cpv_codes (
	version_id VARCHAR(64) NOT NULL, 
	code VARCHAR(8) NOT NULL, 
	check_digit VARCHAR(1) NOT NULL, 
	label_bg TEXT NOT NULL, 
	CONSTRAINT pk_cpv_codes PRIMARY KEY (version_id, code), 
	CONSTRAINT ck_cpv_codes_official_code CHECK (code ~ '^[0-9]{8}$' AND check_digit ~ '^[0-9]$' AND length(trim(label_bg)) > 0), 
	CONSTRAINT fk_cpv_codes_version_id_cpv_catalogue_versions FOREIGN KEY(version_id) REFERENCES cpv_catalogue_versions (id)
)

;


CREATE TABLE active_company_selections (
	user_id UUID NOT NULL, 
	company_id UUID NOT NULL, 
	generation INTEGER DEFAULT '1' NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_active_company_selections PRIMARY KEY (user_id), 
	CONSTRAINT fk_active_company_selections_company_id_companies FOREIGN KEY(company_id, user_id) REFERENCES companies (id, owner_user_id), 
	CONSTRAINT ck_active_company_selections_generation CHECK (generation > 0), 
	CONSTRAINT fk_active_company_selections_user_id_telegram_users FOREIGN KEY(user_id) REFERENCES telegram_users (id)
)

;


CREATE TABLE classification_intents (
	id UUID NOT NULL, 
	company_id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	status VARCHAR(16) DEFAULT 'PENDING' NOT NULL, 
	request_key_hash VARCHAR(64) NOT NULL, 
	input_digest VARCHAR(64) NOT NULL, 
	base_profile_revision INTEGER NOT NULL, 
	base_category_revision INTEGER NOT NULL, 
	company_generation INTEGER NOT NULL, 
	candidate_categories JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_classification_intents PRIMARY KEY (id), 
	CONSTRAINT fk_classification_intents_company_id_companies FOREIGN KEY(company_id, owner_user_id) REFERENCES companies (id, owner_user_id), 
	CONSTRAINT uq_classification_intents_owner_user_id UNIQUE (owner_user_id, kind, request_key_hash), 
	CONSTRAINT ck_classification_intents_state CHECK (kind IN ('CPV_CLASSIFY','CPV_RECLASSIFY') AND status IN ('PENDING','SUCCEEDED','FAILED','CANCELLED')), 
	CONSTRAINT ck_classification_intents_candidate_lifetime CHECK ((status = 'PENDING' AND candidate_categories IS NOT NULL AND jsonb_typeof(candidate_categories) = 'array' AND jsonb_array_length(candidate_categories) BETWEEN 1 AND 2 AND completed_at IS NULL) OR (status <> 'PENDING' AND candidate_categories IS NULL AND completed_at IS NOT NULL)), 
	CONSTRAINT ck_classification_intents_base CHECK (base_profile_revision > 0 AND base_category_revision > 0 AND company_generation > 0)
)

;

CREATE INDEX ix_classification_intents_owner_company ON classification_intents (owner_user_id, company_id);

CREATE UNIQUE INDEX uq_one_pending_classification ON classification_intents (company_id) WHERE status = 'PENDING';


CREATE TABLE company_categories (
	company_id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	category_key VARCHAR(16) NOT NULL, 
	activities_text TEXT NOT NULL, 
	CONSTRAINT pk_company_categories PRIMARY KEY (company_id, category_key), 
	CONSTRAINT fk_company_categories_company_id_companies FOREIGN KEY(company_id, owner_user_id) REFERENCES companies (id, owner_user_id), 
	CONSTRAINT ck_company_categories_activity CHECK (length(trim(activities_text)) BETWEEN 1 AND 2000), 
	CONSTRAINT fk_company_categories_category_key_business_categories FOREIGN KEY(category_key) REFERENCES business_categories (key)
)

;


CREATE TABLE company_cpv_assignments (
	company_id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	code VARCHAR(8) NOT NULL, 
	catalogue_version_id VARCHAR(64) NOT NULL, 
	classifier_revision VARCHAR(64) NOT NULL, 
	assignment_set_id UUID NOT NULL, 
	rationale_reference VARCHAR(128) NOT NULL, 
	CONSTRAINT pk_company_cpv_assignments PRIMARY KEY (company_id, code), 
	CONSTRAINT fk_company_cpv_assignments_company_id_companies FOREIGN KEY(company_id, owner_user_id) REFERENCES companies (id, owner_user_id), 
	CONSTRAINT fk_company_cpv_assignments_catalogue_version_id_cpv_codes FOREIGN KEY(catalogue_version_id, code) REFERENCES cpv_codes (version_id, code)
)

;


CREATE TABLE company_regions (
	company_id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	oblast_code VARCHAR(16) NOT NULL, 
	CONSTRAINT pk_company_regions PRIMARY KEY (company_id, oblast_code), 
	CONSTRAINT fk_company_regions_company_id_companies FOREIGN KEY(company_id, owner_user_id) REFERENCES companies (id, owner_user_id), 
	CONSTRAINT fk_company_regions_oblast_code_oblasts FOREIGN KEY(oblast_code) REFERENCES oblasts (code)
)

;

INSERT INTO business_categories(key,label_bg,display_order) VALUES ('CAT_01','Строителство',1),('CAT_02','Електро',2),('CAT_03','ВиК',3),('CAT_04','ИТ',4),('CAT_05','Медицина',5),('CAT_06','Консултиране',6),('CAT_07','Почистване',7),('CAT_08','Транспорт',8),('CAT_09','Храни',9),('CAT_10','Охрана',10),('CAT_11','Доставки',11),('CAT_12','Енергетика',12);

INSERT INTO oblasts(code,name_bg,display_order) VALUES ('OB_01','Благоевград',1),('OB_02','Бургас',2),('OB_03','Варна',3),('OB_04','Велико Търново',4),('OB_05','Видин',5),('OB_06','Враца',6),('OB_07','Габрово',7),('OB_08','Добрич',8),('OB_09','Кърджали',9),('OB_10','Кюстендил',10),('OB_11','Ловеч',11),('OB_12','Монтана',12),('OB_13','Пазарджик',13),('OB_14','Перник',14),('OB_15','Плевен',15),('OB_16','Пловдив',16),('OB_17','Разград',17),('OB_18','Русе',18),('OB_19','Силистра',19),('OB_20','Сливен',20),('OB_21','Смолян',21),('OB_22','София-град',22),('OB_23','София-област',23),('OB_24','Стара Загора',24),('OB_25','Търговище',25),('OB_26','Хасково',26),('OB_27','Шумен',27),('OB_28','Ямбол',28);
-- Deferred shape checks permit atomic replacements, never partial publication.
CREATE FUNCTION p5_profile_shape() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    c companies%ROWTYPE;
    category_count integer;
    cpv_count integer;
BEGIN
    IF TG_TABLE_NAME = 'companies' THEN
        target := COALESCE(NEW.id, OLD.id);
    ELSE
        target := COALESCE(NEW.company_id, OLD.company_id);
    END IF;
    SELECT * INTO c FROM companies WHERE id = target FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO category_count FROM company_categories WHERE company_id = target;
    SELECT count(*) INTO cpv_count FROM company_cpv_assignments WHERE company_id = target;
    IF c.deleted_at IS NOT NULL THEN
        IF category_count <> 0 OR cpv_count <> 0
           OR EXISTS (SELECT 1 FROM company_regions WHERE company_id = target)
           OR EXISTS (SELECT 1 FROM active_company_selections WHERE company_id = target)
           OR EXISTS (SELECT 1 FROM classification_intents WHERE company_id = target AND status = 'PENDING') THEN
            RAISE EXCEPTION 'Revoked profile retains live private state' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF category_count < 1 OR category_count > 2 THEN
            RAISE EXCEPTION 'A complete profile requires one or two categories' USING ERRCODE = '23514';
        END IF;
        IF c.classification_status = 'PENDING_CPV' AND NOT EXISTS
           (SELECT 1 FROM classification_intents WHERE company_id = target AND kind = 'CPV_CLASSIFY' AND status = 'PENDING') THEN
            RAISE EXCEPTION 'Initial profile requires classification intent' USING ERRCODE = '23514';
        END IF;
        IF (c.classification_status = 'READY' AND (cpv_count < 1 OR cpv_count > 20))
           OR (c.classification_status <> 'READY' AND cpv_count <> 0) THEN
            RAISE EXCEPTION 'Classification publication is incomplete' USING ERRCODE = '23514';
        END IF;
        IF (SELECT count(DISTINCT (catalogue_version_id,classifier_revision,assignment_set_id))
            FROM company_cpv_assignments WHERE company_id = target) > 1 THEN
            RAISE EXCEPTION 'Mixed classification assignment set' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION p5_pending_candidate_shape() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    job classification_intents%ROWTYPE;
    target uuid;
    c companies%ROWTYPE;
    item jsonb;
    n integer;
BEGIN
    target := COALESCE(NEW.id, OLD.id);
    SELECT * INTO job FROM classification_intents WHERE id = target;
    IF NOT FOUND OR job.status <> 'PENDING' THEN RETURN NULL; END IF;
    SELECT * INTO c FROM companies WHERE id = job.company_id FOR UPDATE;
    IF c.deleted_at IS NOT NULL OR c.revocation_generation <> job.company_generation
       OR c.category_revision <> job.base_category_revision THEN
        RAISE EXCEPTION 'Stale or revoked classification intent' USING ERRCODE = '23514';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(job.candidate_categories) LOOP
        IF jsonb_typeof(item) <> 'object' OR jsonb_typeof(item->'category_key') IS DISTINCT FROM 'string'
           OR jsonb_typeof(item->'activity') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'Invalid classification candidate' USING ERRCODE = '23514';
        END IF;
        SELECT count(*) INTO n FROM jsonb_object_keys(item);
        IF n <> 2 OR length(trim(item->>'activity')) NOT BETWEEN 1 AND 2000
           OR NOT EXISTS (SELECT 1 FROM business_categories WHERE key = item->>'category_key') THEN
            RAISE EXCEPTION 'Invalid classification candidate' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    SELECT count(DISTINCT value->>'category_key') INTO n FROM jsonb_array_elements(job.candidate_categories);
    IF n <> jsonb_array_length(job.candidate_categories) THEN
        RAISE EXCEPTION 'Duplicate candidate categories' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER p5_company_shape AFTER INSERT OR UPDATE ON companies
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
CREATE CONSTRAINT TRIGGER p5_category_shape AFTER INSERT OR UPDATE OR DELETE ON company_categories
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
CREATE CONSTRAINT TRIGGER p5_cpv_shape AFTER INSERT OR UPDATE OR DELETE ON company_cpv_assignments
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
CREATE CONSTRAINT TRIGGER p5_region_shape AFTER INSERT OR UPDATE ON company_regions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
CREATE CONSTRAINT TRIGGER p5_selection_shape AFTER INSERT OR UPDATE ON active_company_selections
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
CREATE CONSTRAINT TRIGGER p5_candidate_shape AFTER INSERT OR UPDATE ON classification_intents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_pending_candidate_shape();
CREATE CONSTRAINT TRIGGER p5_intent_profile_shape AFTER INSERT OR UPDATE ON classification_intents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION p5_profile_shape();
