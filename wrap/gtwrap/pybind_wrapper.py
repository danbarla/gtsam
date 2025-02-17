#!/usr/bin/env python3
"""
GTSAM Copyright 2010-2020, Georgia Tech Research Corporation,
Atlanta, Georgia 30332-0415
All Rights Reserved

See LICENSE for the license information

Code generator for wrapping a C++ module with Pybind11
Author: Duy Nguyen Ta, Fan Jiang, Matthew Sklar, Varun Agrawal, and Frank Dellaert
"""

# pylint: disable=too-many-arguments, too-many-instance-attributes, no-self-use, no-else-return, too-many-arguments, unused-format-string-argument, line-too-long

import re

import gtwrap.interface_parser as parser
import gtwrap.template_instantiator as instantiator


class PybindWrapper:
    """
    Class to generate binding code for Pybind11 specifically.
    """
    def __init__(self,
                 module,
                 module_name,
                 top_module_namespaces='',
                 use_boost=False,
                 ignore_classes=(),
                 module_template=""):
        self.module = module
        self.module_name = module_name
        self.top_module_namespaces = top_module_namespaces
        self.use_boost = use_boost
        self.ignore_classes = ignore_classes
        self._serializing_classes = list()
        self.module_template = module_template
        self.python_keywords = ['print', 'lambda']

        # amount of indentation to add before each function/method declaration.
        self.method_indent = '\n' + (' ' * 8)

    def _py_args_names(self, args_list):
        """Set the argument names in Pybind11 format."""
        names = args_list.args_names()
        if names:
            py_args = []
            for arg in args_list.args_list:
                if isinstance(arg.default, str) and arg.default is not None:
                    # string default arg
                    arg.default = ' = "{arg.default}"'.format(arg=arg)
                elif arg.default:  # Other types
                    arg.default = ' = {arg.default}'.format(arg=arg)
                else:
                    arg.default = ''
                argument = 'py::arg("{name}"){default}'.format(
                    name=arg.name, default='{0}'.format(arg.default))
                py_args.append(argument)
            return ", " + ", ".join(py_args)
        else:
            return ''

    def _method_args_signature_with_names(self, args_list):
        """Define the method signature types with the argument names."""
        cpp_types = args_list.to_cpp(self.use_boost)
        names = args_list.args_names()
        types_names = [
            "{} {}".format(ctype, name)
            for ctype, name in zip(cpp_types, names)
        ]

        return ', '.join(types_names)

    def wrap_ctors(self, my_class):
        """Wrap the constructors."""
        res = ""
        for ctor in my_class.ctors:
            res += (
                self.method_indent + '.def(py::init<{args_cpp_types}>()'
                '{py_args_names})'.format(
                    args_cpp_types=", ".join(ctor.args.to_cpp(self.use_boost)),
                    py_args_names=self._py_args_names(ctor.args),
                ))
        return res

    def _wrap_method(self,
                     method,
                     cpp_class,
                     prefix,
                     suffix,
                     method_suffix=""):
        py_method = method.name + method_suffix
        cpp_method = method.to_cpp()

        if cpp_method in ["serialize", "serializable"]:
            if not cpp_class in self._serializing_classes:
                self._serializing_classes.append(cpp_class)
            serialize_method = self.method_indent + \
                ".def(\"serialize\", []({class_inst} self){{ return gtsam::serialize(*self); }})".format(class_inst=cpp_class + '*')
            deserialize_method = self.method_indent + \
                     ".def(\"deserialize\", []({class_inst} self, string serialized){{ gtsam::deserialize(serialized, *self); }}, py::arg(\"serialized\"))" \
                       .format(class_inst=cpp_class + '*')
            return serialize_method + deserialize_method

        if cpp_method == "pickle":
            if not cpp_class in self._serializing_classes:
                raise ValueError(
                    "Cannot pickle a class which is not serializable")
            pickle_method = self.method_indent + \
                ".def(py::pickle({indent}    [](const {cpp_class} &a){{ /* __getstate__: Returns a string that encodes the state of the object */ return py::make_tuple(gtsam::serialize(a)); }},{indent}    [](py::tuple t){{ /* __setstate__ */ {cpp_class} obj; gtsam::deserialize(t[0].cast<std::string>(), obj); return obj; }}))"
            return pickle_method.format(cpp_class=cpp_class,
                                        indent=self.method_indent)

        is_method = isinstance(method, instantiator.InstantiatedMethod)
        is_static = isinstance(method, parser.StaticMethod)
        return_void = method.return_type.is_void()
        args_names = method.args.args_names()
        py_args_names = self._py_args_names(method.args)
        args_signature_with_names = self._method_args_signature_with_names(
            method.args)

        caller = cpp_class + "::" if not is_method else "self->"
        function_call = ('{opt_return} {caller}{function_name}'
                         '({args_names});'.format(
                             opt_return='return' if not return_void else '',
                             caller=caller,
                             function_name=cpp_method,
                             args_names=', '.join(args_names),
                         ))

        ret = ('{prefix}.{cdef}("{py_method}",'
               '[]({opt_self}{opt_comma}{args_signature_with_names}){{'
               '{function_call}'
               '}}'
               '{py_args_names}){suffix}'.format(
                   prefix=prefix,
                   cdef="def_static" if is_static else "def",
                   py_method=py_method if not py_method in self.python_keywords
                   else py_method + "_",
                   opt_self="{cpp_class}* self".format(
                       cpp_class=cpp_class) if is_method else "",
                   opt_comma=', ' if is_method and args_names else '',
                   args_signature_with_names=args_signature_with_names,
                   function_call=function_call,
                   py_args_names=py_args_names,
                   suffix=suffix,
               ))

        # Create __repr__ override
        # We allow all arguments to .print() and let the compiler handle type mismatches.
        if method.name == 'print':
            # Redirect stdout - see pybind docs for why this is a good idea:
            # https://pybind11.readthedocs.io/en/stable/advanced/pycpp/utilities.html#capturing-standard-output-from-ostream
            ret = ret.replace(
                'self->print',
                'py::scoped_ostream_redirect output; self->print')

            # Make __repr__() call print() internally
            ret += '''{prefix}.def("__repr__",
                    [](const {cpp_class}& self{opt_comma}{args_signature_with_names}){{
                        gtsam::RedirectCout redirect;
                        self.{method_name}({method_args});
                        return redirect.str();
                    }}{py_args_names}){suffix}'''.format(
                prefix=prefix,
                cpp_class=cpp_class,
                opt_comma=', ' if args_names else '',
                args_signature_with_names=args_signature_with_names,
                method_name=method.name,
                method_args=", ".join(args_names) if args_names else '',
                py_args_names=py_args_names,
                suffix=suffix)

        return ret

    def wrap_methods(self,
                     methods,
                     cpp_class,
                     prefix='\n' + ' ' * 8,
                     suffix=''):
        """
        Wrap all the methods in the `cpp_class`.

        This function is also used to wrap global functions.
        """
        res = ""
        for method in methods:

            # To avoid type confusion for insert, currently unused
            if method.name == 'insert' and cpp_class == 'gtsam::Values':
                name_list = method.args.args_names()
                type_list = method.args.to_cpp(self.use_boost)
                # inserting non-wrapped value types
                if type_list[0].strip() == 'size_t':
                    method_suffix = '_' + name_list[1].strip()
                    res += self._wrap_method(method=method,
                                             cpp_class=cpp_class,
                                             prefix=prefix,
                                             suffix=suffix,
                                             method_suffix=method_suffix)

            res += self._wrap_method(
                method=method,
                cpp_class=cpp_class,
                prefix=prefix,
                suffix=suffix,
            )

        return res

    def wrap_variable(self,
                      namespace,
                      module_var,
                      variable,
                      prefix='\n' + ' ' * 8):
        """Wrap a variable that's not part of a class (i.e. global)
        """
        variable_value = ""
        if variable.default is None:
            variable_value = variable.name
        else:
            variable_value = variable.default

        return '{prefix}{module_var}.attr("{variable_name}") = {namespace}{variable_value};'.format(
            prefix=prefix,
            module_var=module_var,
            variable_name=variable.name,
            namespace=namespace,
            variable_value=variable_value)

    def wrap_properties(self, properties, cpp_class, prefix='\n' + ' ' * 8):
        """Wrap all the properties in the `cpp_class`."""
        res = ""
        for prop in properties:
            res += ('{prefix}.def_{property}("{property_name}", '
                    '&{cpp_class}::{property_name})'.format(
                        prefix=prefix,
                        property="readonly"
                        if prop.ctype.is_const else "readwrite",
                        cpp_class=cpp_class,
                        property_name=prop.name,
                    ))
        return res

    def wrap_operators(self, operators, cpp_class, prefix='\n' + ' ' * 8):
        """Wrap all the overloaded operators in the `cpp_class`."""
        res = ""
        template = "{prefix}.def({{0}})".format(prefix=prefix)
        for op in operators:
            if op.operator == "[]":  # __getitem__
                res += "{prefix}.def(\"__getitem__\", &{cpp_class}::operator[])".format(
                    prefix=prefix, cpp_class=cpp_class)
            elif op.operator == "()":  # __call__
                res += "{prefix}.def(\"__call__\", &{cpp_class}::operator())".format(
                    prefix=prefix, cpp_class=cpp_class)
            elif op.is_unary:
                res += template.format("{0}py::self".format(op.operator))
            else:
                res += template.format("py::self {0} py::self".format(
                    op.operator))
        return res

    def wrap_enum(self, enum, class_name='', module=None, prefix=' ' * 4):
        """
        Wrap an enum.

        Args:
            enum: The parsed enum to wrap.
            class_name: The class under which the enum is defined.
            prefix: The amount of indentation.
        """
        if module is None:
            module = self._gen_module_var(enum.namespaces())

        cpp_class = enum.cpp_typename().to_cpp()
        if class_name:
            # If class_name is provided, add that as the namespace
            cpp_class = class_name + "::" + cpp_class

        res = '{prefix}py::enum_<{cpp_class}>({module}, "{enum.name}", py::arithmetic())'.format(
            prefix=prefix, module=module, enum=enum, cpp_class=cpp_class)
        for enumerator in enum.enumerators:
            res += '\n{prefix}    .value("{enumerator.name}", {cpp_class}::{enumerator.name})'.format(
                prefix=prefix, enumerator=enumerator, cpp_class=cpp_class)
        res += ";\n\n"
        return res

    def wrap_enums(self, enums, instantiated_class, prefix=' ' * 4):
        """Wrap multiple enums defined in a class."""
        cpp_class = instantiated_class.cpp_class()
        module_var = instantiated_class.name.lower()
        res = ''

        for enum in enums:
            res += "\n" + self.wrap_enum(
                enum,
                class_name=cpp_class,
                module=module_var,
                prefix=prefix)
        return res

    def wrap_instantiated_class(
            self, instantiated_class: instantiator.InstantiatedClass):
        """Wrap the class."""
        module_var = self._gen_module_var(instantiated_class.namespaces())
        cpp_class = instantiated_class.cpp_class()
        if cpp_class in self.ignore_classes:
            return ""
        if instantiated_class.parent_class:
            class_parent = "{instantiated_class.parent_class}, ".format(
                instantiated_class=instantiated_class)
        else:
            class_parent = ''

        if instantiated_class.enums:
            # If class has enums, define an instance and set module_var to the instance
            instance_name = instantiated_class.name.lower()
            class_declaration = (
                '\n    py::class_<{cpp_class}, {class_parent}'
                '{shared_ptr_type}::shared_ptr<{cpp_class}>> '
                '{instance_name}({module_var}, "{class_name}");'
                '\n    {instance_name}').format(
                    shared_ptr_type=('boost' if self.use_boost else 'std'),
                    cpp_class=cpp_class,
                    class_name=instantiated_class.name,
                    class_parent=class_parent,
                    instance_name=instance_name,
                    module_var=module_var)
            module_var = instance_name

        else:
            class_declaration = (
                '\n    py::class_<{cpp_class}, {class_parent}'
                '{shared_ptr_type}::shared_ptr<{cpp_class}>>({module_var}, "{class_name}")'
            ).format(shared_ptr_type=('boost' if self.use_boost else 'std'),
                     cpp_class=cpp_class,
                     class_name=instantiated_class.name,
                     class_parent=class_parent,
                     module_var=module_var)

        return ('{class_declaration}'
                '{wrapped_ctors}'
                '{wrapped_methods}'
                '{wrapped_static_methods}'
                '{wrapped_properties}'
                '{wrapped_operators};\n'.format(
                    class_declaration=class_declaration,
                    wrapped_ctors=self.wrap_ctors(instantiated_class),
                    wrapped_methods=self.wrap_methods(
                        instantiated_class.methods, cpp_class),
                    wrapped_static_methods=self.wrap_methods(
                        instantiated_class.static_methods, cpp_class),
                    wrapped_properties=self.wrap_properties(
                        instantiated_class.properties, cpp_class),
                    wrapped_operators=self.wrap_operators(
                        instantiated_class.operators, cpp_class)))

    def wrap_stl_class(self, stl_class):
        """Wrap STL containers."""
        module_var = self._gen_module_var(stl_class.namespaces())
        cpp_class = stl_class.cpp_class()
        if cpp_class in self.ignore_classes:
            return ""

        return (
            '\n    py::class_<{cpp_class}, {class_parent}'
            '{shared_ptr_type}::shared_ptr<{cpp_class}>>({module_var}, "{class_name}")'
            '{wrapped_ctors}'
            '{wrapped_methods}'
            '{wrapped_static_methods}'
            '{wrapped_properties};\n'.format(
                shared_ptr_type=('boost' if self.use_boost else 'std'),
                cpp_class=cpp_class,
                class_name=stl_class.name,
                class_parent=str(stl_class.parent_class) +
                (', ' if stl_class.parent_class else ''),
                module_var=module_var,
                wrapped_ctors=self.wrap_ctors(stl_class),
                wrapped_methods=self.wrap_methods(stl_class.methods,
                                                  cpp_class),
                wrapped_static_methods=self.wrap_methods(
                    stl_class.static_methods, cpp_class),
                wrapped_properties=self.wrap_properties(
                    stl_class.properties, cpp_class),
            ))

    def _partial_match(self, namespaces1, namespaces2):
        for i in range(min(len(namespaces1), len(namespaces2))):
            if namespaces1[i] != namespaces2[i]:
                return False
        return True

    def _gen_module_var(self, namespaces):
        """Get the Pybind11 module name from the namespaces."""
        # We skip the first value in namespaces since it is empty
        sub_module_namespaces = namespaces[len(self.top_module_namespaces):]
        return "m_{}".format('_'.join(sub_module_namespaces))

    def _add_namespaces(self, name, namespaces):
        if namespaces:
            # Ignore the first empty global namespace.
            idx = 1 if not namespaces[0] else 0
            return '::'.join(namespaces[idx:] + [name])
        else:
            return name

    def wrap_namespace(self, namespace):
        """Wrap the complete `namespace`."""
        wrapped = ""
        includes = ""

        namespaces = namespace.full_namespaces()
        if not self._partial_match(namespaces, self.top_module_namespaces):
            return "", ""

        if len(namespaces) < len(self.top_module_namespaces):
            for element in namespace.content:
                if isinstance(element, parser.Include):
                    include = "{}\n".format(element)
                    # replace the angle brackets with quotes
                    include = include.replace('<', '"').replace('>', '"')
                    includes += include
                if isinstance(element, parser.Namespace):
                    (
                        wrapped_namespace,
                        includes_namespace,
                    ) = self.wrap_namespace(  # noqa
                        element)
                    wrapped += wrapped_namespace
                    includes += includes_namespace
        else:
            module_var = self._gen_module_var(namespaces)

            if len(namespaces) > len(self.top_module_namespaces):
                wrapped += (
                    ' ' * 4 + 'pybind11::module {module_var} = '
                    '{parent_module_var}.def_submodule("{namespace}", "'
                    '{namespace} submodule");\n'.format(
                        module_var=module_var,
                        namespace=namespace.name,
                        parent_module_var=self._gen_module_var(
                            namespaces[:-1]),
                    ))

            # Wrap an include statement, namespace, class or enum
            for element in namespace.content:
                if isinstance(element, parser.Include):
                    include = "{}\n".format(element)
                    # replace the angle brackets with quotes
                    include = include.replace('<', '"').replace('>', '"')
                    includes += include
                elif isinstance(element, parser.Namespace):
                    wrapped_namespace, includes_namespace = self.wrap_namespace(
                        element)
                    wrapped += wrapped_namespace
                    includes += includes_namespace

                elif isinstance(element, instantiator.InstantiatedClass):
                    wrapped += self.wrap_instantiated_class(element)
                    wrapped += self.wrap_enums(element.enums, element)

                elif isinstance(element, parser.Variable):
                    variable_namespace = self._add_namespaces('', namespaces)
                    wrapped += self.wrap_variable(namespace=variable_namespace,
                                                  module_var=module_var,
                                                  variable=element,
                                                  prefix='\n' + ' ' * 4)

                elif isinstance(element, parser.Enum):
                    wrapped += self.wrap_enum(element)

            # Global functions.
            all_funcs = [
                func for func in namespace.content
                if isinstance(func, (parser.GlobalFunction,
                                     instantiator.InstantiatedGlobalFunction))
            ]
            wrapped += self.wrap_methods(
                all_funcs,
                self._add_namespaces('', namespaces)[:-2],
                prefix='\n' + ' ' * 4 + module_var,
                suffix=';',
            )
        return wrapped, includes

    def wrap(self):
        """Wrap the code in the interface file."""
        wrapped_namespace, includes = self.wrap_namespace(self.module)

        # Export classes for serialization.
        boost_class_export = ""
        for cpp_class in self._serializing_classes:
            new_name = cpp_class
            # The boost's macro doesn't like commas, so we have to typedef.
            if ',' in cpp_class:
                new_name = re.sub("[,:<> ]", "", cpp_class)
                boost_class_export += "typedef {cpp_class} {new_name};\n".format(  # noqa
                    cpp_class=cpp_class,
                    new_name=new_name,
                )
            boost_class_export += "BOOST_CLASS_EXPORT({new_name})\n".format(
                new_name=new_name, )

        holder_type = "PYBIND11_DECLARE_HOLDER_TYPE(TYPE_PLACEHOLDER_DONOTUSE, " \
                      "{shared_ptr_type}::shared_ptr<TYPE_PLACEHOLDER_DONOTUSE>);"
        include_boost = "#include <boost/shared_ptr.hpp>" if self.use_boost else ""

        return self.module_template.format(
            include_boost=include_boost,
            module_name=self.module_name,
            includes=includes,
            holder_type=holder_type.format(
                shared_ptr_type=('boost' if self.use_boost else 'std'))
            if self.use_boost else "",
            wrapped_namespace=wrapped_namespace,
            boost_class_export=boost_class_export,
        )
