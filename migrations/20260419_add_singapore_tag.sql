-- Add 'singapore' to the allowed tags for uwusuite_apps
ALTER TABLE public.uwusuite_apps DROP CONSTRAINT uwusuite_apps_tags_chk;
ALTER TABLE public.uwusuite_apps ADD CONSTRAINT uwusuite_apps_tags_chk CHECK (
  tags <@ array['tools'::text, 'games'::text, 'bots'::text, 'singapore'::text]
);
