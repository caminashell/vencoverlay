from vencoverlay.window import WindowController


class Surface:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def raise_window(self) -> None:
        self.calls.append("raise")

    def destroy(self) -> None:
        self.calls.append("destroy")


def test_initially_hidden() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=False)
    assert controller.maintain_stacking()
    assert surface.calls == []


def test_mapping_transitions() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    controller.sync(visible=True)
    controller.sync(visible=False)
    controller.sync(visible=False)
    controller.sync(visible=True)
    assert surface.calls == ["show", "hide", "show"]


def test_raise_only_when_visible() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    assert controller.maintain_stacking()
    controller.sync(visible=False)
    assert controller.maintain_stacking()
    assert surface.calls == ["show", "raise", "hide"]


def test_shutdown_is_idempotent() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    controller.close()
    controller.close()
    controller.sync(visible=True)
    assert not controller.maintain_stacking()
    assert not controller.visible
    assert surface.calls == ["show", "destroy"]
