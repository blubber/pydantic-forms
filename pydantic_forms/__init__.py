import datetime
import typing as t
from dataclasses import dataclass
from enum import Enum
from functools import cache, cached_property

from pydantic import BaseModel, Field, types
from pydantic.error_wrappers import ErrorWrapper, ValidationError
from pydantic.errors import PydanticValueError
from pydantic.fields import ModelField
from pydantic.main import ModelMetaclass

ConfigType = t.Type["BaseConfig"]
DataType = dict[str, t.Any]
ErrorType = dict[str, list[PydanticValueError]]


@t._LiteralSpecialForm
@t._tp_cache(typed=True)
def Choice(self: t.Any, *options: tuple[tuple[str, str], ...]) -> t.Any:
    literal = t._LiteralGenericAlias(self, tuple(o for o, _ in options))
    literal.__origin__ = t.Literal
    literal.__options__ = options
    return literal


class BaseConfig:
    model: t.Type[BaseModel]


def inherit_config(self_config: ConfigType, parent_config: ConfigType):
    if self_config == parent_config:
        base_classes: tuple[ConfigType, ...] = (self_config,)
    else:
        base_classes = self_config, parent_config

    return type("Config", base_classes, {})


class FormMetaclass(type):
    def __new__(cls, name, bases, namespace):
        config = BaseConfig

        for base in reversed(bases):
            if (
                _is_base_form_class_defined
                and issubclass(base, BaseForm)
                and base != BaseForm
            ):
                config = inherit_config(base.__config__, config)

        if config_from_namespace := namespace.pop("Config", None):
            config = inherit_config(config_from_namespace, config)

        namespace["__config__"] = config

        return super().__new__(cls, name, bases, namespace)


_is_base_form_class_defined = False


class BaseForm(metaclass=FormMetaclass):

    __config__: t.ClassVar[ConfigType]

    def __init__(self, data: DataType | None = None, initial: BaseModel | None = None):
        # XXX: Pass form instance to widgets
        #      Use a BaseModel instance as initial
        #      get_field_value should return the model default if no value is set
        self._data = data
        self._error: ValidationError | None = None
        self._model: BaseModel | None = None

        if initial is not None and not isinstance(initial, self.__config__.model):
            raise ValueError(
                f"Initial must be of type {self.__config__.model} (Config.model)"
            )

        self._initial = initial
        self._model_data = self.get_model_data()

    @property
    def data(self) -> BaseModel:
        self.clean()
        return t.cast(BaseModel, self._model)

    @property
    def errors(self) -> ErrorType | None:
        try:
            self.clean()
        except ValidationError:
            pass
        else:
            return None

        errors: ErrorType = {}
        error = t.cast(ValidationError, self._error)
        for exc in error.raw_errors:
            assert isinstance(exc, ErrorWrapper)
            assert isinstance(exc.exc, PydanticValueError)
            (field,) = exc.loc_tuple()
            errors.setdefault(t.cast(str, field), []).append(exc.exc)

        return errors

    def clean(self) -> None:
        if self._error is not None:
            # The form is bound but contains errors
            raise self._error

        if self._model is not None:
            # The form is bound with no errors
            return

        data = self.process_data(self.get_model_data())
        model_class = self.get_model_class()

        try:
            self._model = model_class(**data)
        except ValidationError as ex:
            self._error = ex
            raise

        self._model_data = self.get_model_data()

    def is_valid(self) -> bool:
        try:
            self.clean()
        except ValidationError:
            return False
        else:
            return True

    def get_model_class(self) -> t.Type[BaseModel]:
        return self.__config__.model

    def get_model_data(self) -> DataType:
        if self._model is not None:
            return self._model.dict()

        data = {}

        if self._initial:
            data.update(self._initial.dict())

        if self._data is not None:
            data.update(self._data)

        return data

    def process_data(self, data: DataType) -> DataType:
        return data

    def value_of(self, field: str) -> t.Any:
        return self._model_data.get(
            field, self.__config__.model.__fields__[field].default
        )


_is_base_form_class_defined = True


class WidgetMetaclass(ModelMetaclass):
    def __new__(cls, name, bases, namespace):
        exclude_attrs: set[str] = set()

        for base in reversed(bases):
            exclude_attrs.update(getattr(base, "__exclude_attrs__", []))

        exclude_attrs.update(namespace.get("__exclude_attrs__", []))
        namespace["__exclude_attrs__"] = exclude_attrs
        return super().__new__(cls, name, bases, namespace)


class Widget(BaseModel, metaclass=WidgetMetaclass):

    name: str
    id: str | None
    value: t.Any
    required: bool = False
    class_: str | None = Field(None, alias="class")
    autofocus: bool = False
    autocomplete: str | None = None
    placeholder: str | None = None

    __exclude_attrs__ = {"class_", "value"}

    @classmethod
    def additional_kwargs(cls, field: ModelField) -> dict[str, str | bool | None]:
        return {}

    def format_value(self, value: t.Any) -> str:
        if value is None:
            return ""
        return str(value)

    def attrs(self) -> dict[str, str | bool | None]:
        return {
            **{
                key: value
                for key, value in self.dict().items()
                if key not in self.__exclude_attrs__
            },
            **{"class": self.class_, "value": self.format_value(self.value)},
        }


class Input(Widget):

    type: str = "text"
    list: str | None = None


class String(Input):
    pass


class Password(String):

    type: str = "password"

    def format_value(self, value: types.SecretStr | None) -> str:
        if value is None:
            return ""
        return value.get_secret_value()


class Number(Input):

    type = "number"
    min: str | None = None
    max: str | None = None
    step: str | None = None

    @classmethod
    def additional_kwargs(self, field: ModelField) -> dict[str, str | bool | None]:
        kwargs = super().additional_kwargs(field)

        if (ge := getattr(field.type_, "ge", None)) is not None:
            kwargs["min"] = str(ge)
        if (le := getattr(field.type_, "le", None)) is not None:
            kwargs["max"] = str(le)

        return kwargs


class StrftimeMixin(BaseModel):

    format: str
    __exclude_attrs__ = {"format"}

    def format_value(self, value: t.Any) -> str:
        if value is None:
            return ""

        return self.value.strftime(format)



class Date(StrftimeMixin, Input):

    type: str = "date"
    format: str = "%Y-%m-%d"



class Time(StrftimeMixin, Input):

    type: str = "date"
    format: str = "%H:%M:%S"


class DateTime(StrftimeMixin, Input):

    type: str = "date"
    format: str = "%Y-%m-%dT%H{%M:%S"


class Checkbox(Input):

    type: str = "checkbox"

    def attrs(self) -> dict[str, str | bool | None]:
        attrs = super().attrs()
        attrs.pop('value')
        attrs['checked'] = self.value
        return attrs



class BaseRenderer:
    def render_widget(self, widget: "Widget") -> str:
        raise NotImplementedError()


class StringRenderer(BaseRenderer):
    def render_attrs(self, widget: "Widget") -> str:
        return " ".join(
            f"{key}" if value is True else f'{key}="{value}"'
            for key, value in widget.attrs().items()
            if value is not None and value is not False
        )

    def render_widget(self, widget: "Widget") -> str:
        attrs = self.render_attrs(widget)
        tag = "input"

        return f"<{tag} {attrs}>"


class BoundField:
    def __init__(self, field: ModelField, form: "Form") -> None:
        self.field = field
        self.form = form

    @property
    def required(self) -> bool:
        return bool(self.field.required)

    @property
    def value(self) -> t.Any:
        return self.form.value_of(self.field.name)

    @cached_property
    def widget(self) -> Widget:
        widget_class = self.widget_class()
        kwargs = self.widget_kwargs(widget_class)
        return widget_class(**kwargs)

    def widget_class(self) -> t.Type[Widget]:
        if self.field.name in self.form.__config__.widget_classes:
            return self.form.__config__.widget_classes[self.field.name]

        if issubclass(self.field.type_, bool):
            return Checkbox
        elif issubclass(self.field.type_, (int, float)):
            return Number
        elif self.field.type_ == datetime.date:
            return Date
        elif self.field.type_ == datetime.time:
            return Time
        elif self.field.type_ == datetime.datetime:
            return DateTime
        elif issubclass(self.field.type_, types.SecretStr):
            return Password

        return Input

    def widget_kwargs(
        self, widget_class: t.Type[Widget]
    ) -> dict[str, str | bool | None]:
        name = self.form.prefix_name(self.field.name)

        kwargs: dict[str, str | bool | None] = {
            "name": name,
            "id": f"id_{name}",
            "required": self.required,
            "value": self.value,
        }
        kwargs.update(widget_class.additional_kwargs(self.field))
        kwargs.update(self.form.__config__.widget_kwargs.get(self.field.name, {}))
        return kwargs

    def render_widget(self) -> str:
        return self.form.__config__.renderer.render_widget(self.widget)

    def __str__(self) -> str:
        return self.render_widget()


class Form(BaseForm):
    @cache
    def bind_field(self, name: str) -> BoundField:
        self.clean()

        field = self.__config__.model.__fields__[name]
        return BoundField(field, self)

    def prefix_name(self, name: str) -> str:
        return f"{self.__config__.prefix}{name}"

    def __getitem__(self, name: str) -> BoundField:
        return self.bind_field(name)

    class Config(BaseConfig):
        prefix = ""
        renderer: BaseRenderer = StringRenderer()
        widget_classes: dict[str, t.Type[Widget]] = {}
        widget_kwargs: dict[str, dict[str, str | bool | None]] = {}

    __config__: t.ClassVar[t.Type[Config]]
