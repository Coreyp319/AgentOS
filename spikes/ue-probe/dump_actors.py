# dump_actors.py — list every actor in CalmWallpaper (label :: class) + key light/
# sky/fog props, so we can see exactly what is lighting the scene. Diagnostic only.
import unreal

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _log(m):
    unreal.log("[dump] " + str(m))


_log("==== ACTOR DUMP START ====")
for a in eas.get_all_level_actors():
    try:
        label = a.get_actor_label()
        cls = a.get_class().get_name()
        extra = ""
        comp = a.get_component_by_class(unreal.LightComponent)
        if comp:
            try:
                extra = " intensity={} color={}".format(
                    comp.get_editor_property("intensity"),
                    comp.get_editor_property("light_color"))
            except Exception:
                pass
        _log("{} :: {}{}".format(label, cls, extra))
    except Exception as exc:  # noqa: BLE001
        _log("?? error on actor: {}".format(exc))
_log("==== ACTOR DUMP DONE ====")
