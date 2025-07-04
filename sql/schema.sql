--
-- Table structure for table "architectures"
--

DROP TABLE IF EXISTS "architectures" CASCADE;
CREATE TABLE "architectures" (
  "id" SERIAL PRIMARY KEY,
  "name" VARCHAR(255) NOT NULL,
  CONSTRAINT "architectures_name_key" UNIQUE ("name")
);

--
-- Table structure for table "distributions"
--

DROP TABLE IF EXISTS "distributions" CASCADE;
CREATE TABLE "distributions" (
  "id" SERIAL PRIMARY KEY,
  "name" VARCHAR(255) NOT NULL,
  "version" VARCHAR(255) NOT NULL,
  CONSTRAINT "distributions_name_version_key" UNIQUE ("name", "version")
);

--
-- Table structure for table "platforms"
--

DROP TABLE IF EXISTS "platforms" CASCADE;
CREATE TABLE "platforms" (
  "id" SERIAL PRIMARY KEY,
  "distribution_id" INTEGER NOT NULL,
  "architecture_id" INTEGER NOT NULL,
  CONSTRAINT "platforms_distribution_architecture_key" UNIQUE ("distribution_id", "architecture_id"),
  FOREIGN KEY ("distribution_id") REFERENCES "distributions" ("id"),
  FOREIGN KEY ("architecture_id") REFERENCES "architectures" ("id")
);
CREATE INDEX "platforms_distribution_id_idx" ON "platforms" ("distribution_id");
CREATE INDEX "platforms_architecture_id_idx" ON "platforms" ("architecture_id");

--
-- Table structure for table "providers"
--

DROP TABLE IF EXISTS "providers" CASCADE;
CREATE TABLE "providers" (
  "id" SERIAL PRIMARY KEY,
  "url" VARCHAR(255) NOT NULL,
  CONSTRAINT "providers_url_key" UNIQUE ("url")
);

--
-- Table structure for table "repositories"
--

DROP TABLE IF EXISTS "repositories" CASCADE;
CREATE TABLE "repositories" (
  "id" SERIAL PRIMARY KEY,
  "provider_id" INTEGER NOT NULL,
  "platform_id" INTEGER NOT NULL,
  "type" INTEGER NOT NULL,
  "name" VARCHAR(255) NOT NULL,
  "destination" VARCHAR(255) NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
  FOREIGN KEY ("provider_id") REFERENCES "providers" ("id"),
  FOREIGN KEY ("platform_id") REFERENCES "platforms" ("id")
);
CREATE INDEX "repositories_provider_id_idx" ON "repositories" ("provider_id");
CREATE INDEX "repositories_platform_id_idx" ON "repositories" ("platform_id");
CREATE INDEX "repositories_name_idx" ON "repositories" ("name");
CREATE INDEX "repositories_provider_platform_idx" ON "repositories" ("provider_id", "platform_id");
CREATE INDEX "repositories_enabled_idx" ON "repositories" ("enabled") WHERE "enabled" = TRUE;

--
-- Table structure for table "builds" (with partitioning)
--

DROP TABLE IF EXISTS "builds" CASCADE;
CREATE TABLE "builds" (
  "id" SERIAL,
  "repository_id" INTEGER NOT NULL,
  "platform_id" INTEGER NOT NULL,
  "tag" VARCHAR(255) NOT NULL,
  "date" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "status" INTEGER NOT NULL DEFAULT 0,  -- 0=scheduled
  "output" TEXT,
  PRIMARY KEY ("id", "date"),  -- Chiave composita necessaria per il partizionamento
  FOREIGN KEY ("repository_id") REFERENCES "repositories" ("id"),
  FOREIGN KEY ("platform_id") REFERENCES "platforms" ("id")
) PARTITION BY RANGE ("date");

-- Efficiency configurations
ALTER TABLE "builds" ALTER COLUMN "output" SET STORAGE EXTERNAL;
ALTER TABLE "builds" ALTER COLUMN "output" SET COMPRESSION pglz;

-- Create efficient indexes for builds table
CREATE INDEX "builds_repository_id_idx" ON "builds" ("repository_id");
CREATE INDEX "builds_platform_id_idx" ON "builds" ("platform_id");
CREATE INDEX "builds_date_brin_idx" ON "builds" USING BRIN ("date");
CREATE INDEX "builds_repo_status_idx" ON "builds" ("repository_id", "status");
CREATE INDEX "builds_status_date_idx" ON "builds" ("status", "date");
CREATE INDEX "builds_date_year_idx" ON "builds" (EXTRACT(YEAR FROM "date"));
CREATE INDEX "builds_date_month_idx" ON "builds" (EXTRACT(MONTH FROM "date"));

-- Funzione per creare automaticamente nuove partizioni per builds
CREATE OR REPLACE FUNCTION create_new_builds_partition()
RETURNS TRIGGER AS $$
DECLARE
  partition_date TEXT;
  partition_name TEXT;
  start_date DATE;
  end_date DATE;
BEGIN
  -- Crea partizioni mensili
  partition_date := to_char(date_trunc('month', NEW.date), 'YYYY_MM');
  partition_name := 'builds_' || partition_date;
  start_date := date_trunc('month', NEW.date);
  end_date := start_date + interval '1 month';
  
  -- Verifica se la partizione esiste già
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    RAISE NOTICE 'Creazione nuova partizione % per intervallo % - %', 
                  partition_name, start_date, end_date;
                  
    EXECUTE format('CREATE TABLE %I PARTITION OF builds
                    FOR VALUES FROM (%L) TO (%L)',
                    partition_name, start_date, end_date);
                    
    -- Crea indici sulla nuova partizione
    EXECUTE format('CREATE INDEX %I ON %I ("repository_id")',
                  partition_name || '_repo_idx', partition_name);
                  
    EXECUTE format('CREATE INDEX %I ON %I ("status", "date")',
                  partition_name || '_status_date_idx', partition_name);
  END IF;
  
  RETURN NEW;
EXCEPTION
  WHEN OTHERS THEN
    RAISE WARNING 'Errore nella creazione della partizione builds: %', SQLERRM;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attiva il trigger sulla tabella builds
CREATE TRIGGER create_builds_partition_trigger
  BEFORE INSERT ON builds
  FOR EACH ROW
  EXECUTE FUNCTION create_new_builds_partition();

--
-- Table structure for table "artifacts" (with partitioning)
--

DROP TABLE IF EXISTS "artifacts" CASCADE;
CREATE TABLE "artifacts" (
  "id" SERIAL,
  "build_id" INTEGER NOT NULL,
  "build_date" TIMESTAMP NOT NULL,
  "hash" VARCHAR(255),
  "filename" VARCHAR(255) NOT NULL,
  "symlink_target" VARCHAR(255),
  PRIMARY KEY ("id", "build_id"),
  FOREIGN KEY ("build_id", "build_date") REFERENCES "builds" ("id", "date") ON DELETE CASCADE
) PARTITION BY RANGE ("build_id");

-- Create efficient indexes for artifacts table
CREATE INDEX "artifacts_build_id_brin_idx" ON "artifacts" USING BRIN ("build_id");
CREATE INDEX "artifacts_hash_idx" ON "artifacts" ("hash") WHERE "hash" IS NOT NULL;
CREATE INDEX "artifacts_filename_idx" ON "artifacts" ("filename");
CREATE INDEX "artifacts_filename_pattern_idx" ON "artifacts" (filename text_pattern_ops);

-- Funzione per creare automaticamente nuove partizioni per artifacts
CREATE OR REPLACE FUNCTION create_new_artifacts_partition()
RETURNS TRIGGER AS $$
DECLARE
  partition_name TEXT;
  start_id INTEGER;
  end_id INTEGER;
BEGIN
  -- Crea partizioni ogni 100000 build_id
  start_id := (NEW.build_id / 100000) * 100000;
  end_id := start_id + 100000;
  partition_name := 'artifacts_' || start_id || '_' || end_id;
  
  -- Verifica se la partizione esiste già
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    RAISE NOTICE 'Creazione nuova partizione % per intervallo build_id % - %', 
                  partition_name, start_id, end_id;
                  
    EXECUTE format('CREATE TABLE %I PARTITION OF artifacts
                    FOR VALUES FROM (%s) TO (%s)',
                    partition_name, start_id, end_id);
                    
    -- Crea indici sulla nuova partizione
    EXECUTE format('CREATE INDEX %I ON %I USING BRIN ("build_id")',
                  partition_name || '_build_brin_idx', partition_name);
                  
    EXECUTE format('CREATE INDEX %I ON %I ("filename")',
                  partition_name || '_filename_idx', partition_name);
  END IF;
  
  RETURN NEW;
EXCEPTION
  WHEN OTHERS THEN
    RAISE WARNING 'Errore nella creazione della partizione artifacts: %', SQLERRM;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attiva il trigger sulla tabella artifacts
CREATE TRIGGER create_artifacts_partition_trigger
  BEFORE INSERT ON artifacts
  FOR EACH ROW
  EXECUTE FUNCTION create_new_artifacts_partition();

--
-- Table structure for table "servers"
--

DROP TABLE IF EXISTS "servers" CASCADE;
CREATE TABLE "servers" (
  "id" SERIAL PRIMARY KEY,
  "platform_id" INTEGER NOT NULL,
  "name" VARCHAR(255) NOT NULL,
  "prefix" VARCHAR(255) NOT NULL,
  FOREIGN KEY ("platform_id") REFERENCES "platforms" ("id")
);
CREATE INDEX "servers_platform_id_idx" ON "servers" ("platform_id");

--
-- Table structure for table "facilities"
--

DROP TABLE IF EXISTS "facilities" CASCADE;
CREATE TABLE "facilities" (
  "id" SERIAL PRIMARY KEY,
  "name" VARCHAR(255) NOT NULL,
  CONSTRAINT "facilities_name_key" UNIQUE ("name")
);

--
-- Table structure for table "hosts"
--

DROP TABLE IF EXISTS "hosts" CASCADE;
CREATE TABLE "hosts" (
  "id" SERIAL PRIMARY KEY,
  "facility_id" INTEGER NOT NULL,
  "server_id" INTEGER NOT NULL,
  "platform_id" INTEGER NOT NULL,
  "name" VARCHAR(255) NOT NULL,
  CONSTRAINT "hosts_name_key" UNIQUE ("name"),
  FOREIGN KEY ("facility_id") REFERENCES "facilities" ("id"),
  FOREIGN KEY ("server_id") REFERENCES "servers" ("id"),
  FOREIGN KEY ("platform_id") REFERENCES "platforms" ("id")
);
CREATE INDEX "hosts_facility_id_idx" ON "hosts" ("facility_id");
CREATE INDEX "hosts_server_id_idx" ON "hosts" ("server_id");
CREATE INDEX "hosts_platform_id_idx" ON "hosts" ("platform_id");

--
-- Table structure for table "users"
--

DROP TABLE IF EXISTS "users" CASCADE;
CREATE TABLE "users" (
  "id" SERIAL PRIMARY KEY,
  "name" VARCHAR(255) NOT NULL,
  "admin" BOOLEAN NOT NULL DEFAULT FALSE,
  "notify" BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT "users_name_key" UNIQUE ("name")
);

-- Modifica alla tabella "installations" per renderla una tabella temporale
DROP TABLE IF EXISTS "installations" CASCADE;
CREATE TABLE "installations" (
  "id" SERIAL,
  "host_id" INTEGER NOT NULL,
  "user_id" INTEGER NOT NULL,
  "build_id" INTEGER NOT NULL,
  "build_date" TIMESTAMP NOT NULL,
  "type" INTEGER NOT NULL,
  "install_date" TIMESTAMP NOT NULL,
  "valid_from" TIMESTAMP NOT NULL,
  "valid_to" TIMESTAMP,
  PRIMARY KEY ("id", "valid_from"),
  FOREIGN KEY ("host_id") REFERENCES "hosts" ("id"),
  FOREIGN KEY ("user_id") REFERENCES "users" ("id"),
  FOREIGN KEY ("build_id", "build_date") REFERENCES "builds" ("id", "date") ON DELETE CASCADE
) PARTITION BY RANGE ("valid_from");

-- Indici per la tabella installations
CREATE INDEX "installations_host_id_idx" ON "installations" ("host_id");
CREATE INDEX "installations_user_id_idx" ON "installations" ("user_id");
CREATE INDEX "installations_build_id_idx" ON "installations" ("build_id");
CREATE INDEX "installations_install_date_idx" ON "installations" ("install_date");
CREATE INDEX "installations_valid_range_idx" ON "installations" ("valid_from", "valid_to");
CREATE INDEX "installations_host_date_idx" ON "installations" ("host_id", "install_date");
CREATE INDEX "installations_user_date_idx" ON "installations" ("user_id", "install_date");
CREATE INDEX "installations_current_idx" ON "installations" ("valid_to") WHERE "valid_to" IS NULL;

-- Indice GIST per ricerche di range temporali più efficienti
CREATE INDEX "installations_temporal_idx" ON "installations" USING GIST (
  tsrange("valid_from", "valid_to", '[]')
);

-- Funzione per creare automaticamente nuove partizioni per installations
CREATE OR REPLACE FUNCTION create_new_installations_partition()
RETURNS TRIGGER AS $$
DECLARE
  partition_date TEXT;
  partition_name TEXT;
  start_date DATE;
  end_date DATE;
BEGIN
  -- Determina l'anno dalla data di validità
  partition_date := to_char(date_trunc('year', NEW.valid_from), 'YYYY');
  partition_name := 'installations_' || partition_date;
  start_date := date_trunc('year', NEW.valid_from);
  end_date := start_date + interval '1 year';
  
  -- Verifica se la partizione esiste già
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    RAISE NOTICE 'Creazione nuova partizione % per intervallo % - %', 
                  partition_name, start_date, end_date;
                  
    -- Crea una nuova partizione
    EXECUTE format('CREATE TABLE %I PARTITION OF installations
                    FOR VALUES FROM (%L) TO (%L)',
                    partition_name,
                    start_date,
                    end_date);
                    
    -- Crea indici sulla nuova partizione
    EXECUTE format('CREATE INDEX %I ON %I ("host_id")',
                  partition_name || '_host_idx', partition_name);
                  
    EXECUTE format('CREATE INDEX %I ON %I USING BRIN ("valid_from", "valid_to")',
                  partition_name || '_temporal_brin_idx', partition_name);
                  
    -- Crea indice GIST per range temporali sulla partizione
    EXECUTE format('CREATE INDEX %I ON %I USING GIST (tsrange("valid_from", "valid_to", ''[]''))',
                  partition_name || '_gist_idx', partition_name);
  END IF;
  
  RETURN NEW;
EXCEPTION
  WHEN OTHERS THEN
    RAISE WARNING 'Errore nella creazione della partizione: %', SQLERRM;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attiva il trigger sulla tabella installations
CREATE TRIGGER create_installations_partition_trigger
  BEFORE INSERT ON installations
  FOR EACH ROW
  EXECUTE FUNCTION create_new_installations_partition();

-- Funzione per gestire gli aggiornamenti temporali delle installazioni
-- Quando un record viene aggiornato, questa funzione:
-- 1. Chiude il periodo di validità del record corrente impostando valid_to = NOW()
-- 2. Crea un nuovo record con i valori aggiornati e valid_from = NOW()
-- 3. Mantiene la storia completa delle modifiche nel tempo
-- 
-- Questo pattern implementa il "Slowly Changing Dimension Type 2" (SCD2)
-- per mantenere una traccia storica completa di tutte le modifiche
CREATE OR REPLACE FUNCTION installation_temporal_update()
RETURNS TRIGGER AS $$
BEGIN
  -- Verifica che esista un record attivo (valid_to IS NULL indica record corrente)
  IF NOT EXISTS (
    SELECT 1 FROM installations 
    WHERE id = OLD.id AND valid_to IS NULL
  ) THEN
    RAISE EXCEPTION 'Nessun record attivo trovato per installation_id %', OLD.id;
  END IF;
  
  -- Chiude il record esistente impostando il timestamp di fine validità
  UPDATE installations 
  SET valid_to = CURRENT_TIMESTAMP
  WHERE id = OLD.id AND valid_to IS NULL;
  
  -- Inserisce un nuovo record con i valori aggiornati
  -- Il nuovo record avrà valid_from = NOW() e valid_to = NULL (record attivo)
  INSERT INTO installations (
    host_id, user_id, build_id, build_date, type, install_date, valid_from, valid_to
  ) VALUES (
    NEW.host_id, NEW.user_id, NEW.build_id, NEW.build_date, NEW.type, 
    NEW.install_date, CURRENT_TIMESTAMP, NULL
  );
  
  -- Ritorna NULL per impedire l'update diretto del record originale
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Trigger per gestire gli aggiornamenti temporali
CREATE TRIGGER installations_temporal_update_trigger
  BEFORE UPDATE ON installations
  FOR EACH ROW
  WHEN (OLD.* IS DISTINCT FROM NEW.*)
  EXECUTE FUNCTION installation_temporal_update();

-- Vista per ottenere la versione corrente delle installazioni
-- Filtra solo i record con valid_to = NULL (record attualmente validi)
CREATE OR REPLACE VIEW current_installations AS
SELECT id, host_id, user_id, build_id, build_date, type, install_date, valid_from
FROM installations
WHERE valid_to IS NULL;

-- Funzione per recuperare lo stato di un'installazione in un dato momento
-- Utile per audit e analisi storiche: "com'era configurato l'host X alla data Y?"
DROP FUNCTION IF EXISTS get_installation_at_time(INTEGER, TIMESTAMP);

CREATE OR REPLACE FUNCTION get_installation_at_time(
  installation_id INTEGER, 
  point_in_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
RETURNS TABLE (
  id INTEGER,
  host_id INTEGER,
  user_id INTEGER,
  build_id INTEGER,
  build_date TIMESTAMP,
  type INTEGER,
  install_date TIMESTAMP,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP
) AS $$
BEGIN
  RETURN QUERY
  SELECT i.*
  FROM installations i
  WHERE i.id = installation_id
  AND point_in_time >= i.valid_from
  AND (i.valid_to IS NULL OR point_in_time < i.valid_to);
END;
$$ LANGUAGE plpgsql STABLE;

-- Funzione per recuperare la storia completa di un'installazione
-- Mostra tutte le modifiche nel tempo ordinate dalla più recente
-- Drop delle versioni precedenti per evitare conflitti
DROP FUNCTION IF EXISTS get_installation_history(INTEGER);
DROP FUNCTION IF EXISTS get_installation_history(INTEGER, INTEGER);

CREATE OR REPLACE FUNCTION get_installation_history(
  installation_id INTEGER,
  limit_rows INTEGER DEFAULT NULL
)
RETURNS TABLE (
  id INTEGER,
  host_id INTEGER,
  user_id INTEGER,
  build_id INTEGER,
  build_date TIMESTAMP,
  type INTEGER,
  install_date TIMESTAMP,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP,
  duration INTERVAL
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    i.*,
    COALESCE(i.valid_to, CURRENT_TIMESTAMP) - i.valid_from AS duration
  FROM installations i
  WHERE i.id = installation_id
  ORDER BY i.valid_from DESC
  LIMIT limit_rows;
END;
$$ LANGUAGE plpgsql STABLE;
COMMENT ON FUNCTION get_installation_history IS 'Recupera la storia completa delle modifiche di un''installazione con durata di ogni periodo';

-- Creazione di partizioni iniziali per le tabelle partizionate
-- Builds: crea partizione per il mese corrente
DO $$
DECLARE
  current_month DATE := date_trunc('month', CURRENT_DATE);
  next_month DATE := current_month + interval '1 month';
  partition_name TEXT := 'builds_' || to_char(current_month, 'YYYY_MM');
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF builds
                    FOR VALUES FROM (%L) TO (%L)',
                    partition_name, current_month, next_month);
    RAISE NOTICE 'Creata partizione iniziale: %', partition_name;
  END IF;
END $$;

-- Artifacts: crea prima partizione
DO $$
DECLARE
  partition_name TEXT := 'artifacts_0_100000';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF artifacts
                    FOR VALUES FROM (0) TO (100000)',
                    partition_name);
    RAISE NOTICE 'Creata partizione iniziale: %', partition_name;
  END IF;
END $$;

-- Installations: crea partizione per l'anno corrente
DO $$
DECLARE
  current_year DATE := date_trunc('year', CURRENT_DATE);
  next_year DATE := current_year + interval '1 year';
  partition_name TEXT := 'installations_' || to_char(current_year, 'YYYY');
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF installations
                    FOR VALUES FROM (%L) TO (%L)',
                    partition_name, current_year, next_year);
    RAISE NOTICE 'Creata partizione iniziale: %', partition_name;
  END IF;
END $$;

-- Funzione per creare un report delle installazioni attive per facility
DROP FUNCTION IF EXISTS get_active_installations_by_facility();

CREATE OR REPLACE FUNCTION get_active_installations_by_facility()
RETURNS TABLE (
  facility_name VARCHAR(255),
  host_count BIGINT,
  installation_count BIGINT,
  last_installation TIMESTAMP
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    f.name AS facility_name,
    COUNT(DISTINCT h.id) AS host_count,
    COUNT(DISTINCT i.id) AS installation_count,
    MAX(i.install_date) AS last_installation
  FROM facilities f
  LEFT JOIN hosts h ON f.id = h.facility_id
  LEFT JOIN current_installations i ON h.id = i.host_id
  GROUP BY f.id, f.name
  ORDER BY f.name;
END;
$$ LANGUAGE plpgsql STABLE;
COMMENT ON FUNCTION get_active_installations_by_facility IS 'Report delle installazioni attive raggruppate per facility';
