
from collections import OrderedDict
from functools import partial
from textwrap import dedent

from schemec.types import (
    AtomicExp,
    VarExp,
    NumExp,
    BoolExp,
    true,
    false,
    VoidExp,
    void,
    StrExp,
    LamExp,
    AppExp,
    IfExp,
    LetRecExp,
    BeginExp,
    SetExp,
    SetThenExp,
    gensym
    )

__all__ = [
    'gen_cpp',
    'halt',
    'pretty_cpp'
    ]

NUM, LAM, STR = 'NUM', 'LAM', 'STR'
TYPES = [NUM, LAM, STR]

class NumPrimOps:
    binary_fmt = '{dst}->num = {lhs}->num {op} {rhs}->num;'
    binary_ops = {
        '+': '+',
        '-': '-',
        '*': '*',
        '=': '=='
        }

    unary_fmt = '{dst}->num = {lhs}->num {op};'
    unary_ops = {
        'zero?': '== 0'
        }

    @staticmethod
    def __call__(op, dst, lhs, rhs=None):
        try:
            if rhs is None:
                op = NumPrimOps.unary_ops[op]
                return (NUM, NumPrimOps.unary_fmt.format(
                    dst=dst, lhs=lhs, op=op
                    ))
            else:
                op = NumPrimOps.binary_ops[op]
                return (NUM, NumPrimOps.binary_fmt.format(
                    dst=dst, lhs=lhs, op=op, rhs=rhs
                    ))
        except KeyError:
            raise RuntimeError('unimplemented primitive number operation: {0}'.format(str(op)))

    @staticmethod
    def __contains__(key):
        return (
            key in NumPrimOps.binary_ops or
            key in NumPrimOps.unary_ops
            )

    @staticmethod
    def __getitem__(key):
        if key in NumPrimOps.binary_ops:
            return NumPrimOps.binary_ops[key]
        elif key in NumPrimOps.unary_ops:
            return NumPrimOps.unary_ops[key]
        else:
            raise KeyError(key)

num_primops = NumPrimOps()

class StrPrimOps:
    binary_ops = {
        'string-append': dedent('''\
            {dst}->str = std::shared_ptr<std::string>({lhs}->str);
            {dst}->str.append({rhs}->str);'''
            ),
        'string=?': '{dst}->num = {lhs}->str.compare({rhs}->str) != 0;'
        }

    @staticmethod
    def __call__(op, dst, lhs, rhs=None):
        try:
            if rhs is None:
                raise KeyError(op)
            else:
                return (STR, StrPrimOps.binary_ops[op].format(
                    dst=dst, lhs=lhs, rhs=rhs
                    ))
        except KeyError:
            raise RuntimeError('unimplemented primitive operation: {0}'.format(str(op)))

    @staticmethod
    def __contains__(key):
        return key in StrPrimOps.binary_ops

    @staticmethod
    def __getitem__(key):
        if key in StrPrimOps.binary_ops:
            return StrPrimOps.binary_ops[key]
        else:
            raise KeyError(key)

str_primops = StrPrimOps()

def is_primop(op):
    if op in num_primops or op in str_primops:
        return True
    else:
        return False

def gen_primop(op, dst, *args):
    if op in num_primops:
        return num_primops(op, dst, *args)
    elif op in str_primops:
        return str_primops(op, dst, *args)
    else:
        raise KeyError(op)

def unimplemented(exp):
    raise RuntimeError('unimplemented expression type: {0}'.format(str(type(exp))))

def compute_holes(rootExp):
    holes_dict = {}
    def find_holes(holes, exp):
        if isinstance(exp, LamExp):
            holes_dict[exp] = list(holes - set(exp.argExps))
            holes -= set(exp.argExps)
        elif isinstance(exp, VarExp):
            if not is_primop(exp.name):
                holes.add(exp)
        return exp
    rootExp.map(partial(find_holes, set()))
    return holes_dict

class CppCode:
    def __init__(self, typ, code, decls):
        self.typ = typ
        self.code = code
        self.decls = decls
    def __str__(self):
        return self.code
    def __repr__(self):
        return self.code
    def map(self, f):
        return f(self)
    @property
    def decls_ops(self):
        if len(self.decls):
            decls, ops = zip(*self.decls)
        else:
            decls, ops = [], []
        return (
            '\n'.join(d for d in decls if d),
            '\n'.join(o for o in ops if o)
            )

class LambdaGenCpp:
    def __init__(self, exp):
        self.holes = compute_holes(exp)
        self._decls = OrderedDict()
        self.nargs = set()
    def __getitem__(self, exp):
        assert isinstance(exp, LamExp)
        if exp.name not in self._decls:
            self.nargs.add(len(exp.argExps))
            holes = self.holes[exp]
            decls, ops = exp.bodyExp.decls_ops
            apply_args = ', '.join('SCHEMETYPE_T {0}'.format(str(arg)) for arg in exp.argExps)
            self._decls[exp] = (
                dedent('''\
                    class {cls} : public lambda_t {{
                     public:
                      {cls}({init_args}) : {init_asmts} {{ }}
                      ~{cls}() {{ }};
                      THUNK_T operator()({apply_args}) const;
                     private:
                      {priv}
                    }};''').format(
                        cls=exp.name,
                        init_args=', '.join('SCHEMETYPE_T {0}'.format(hole.name) for hole in holes),
                        init_asmts=', '.join('{0}({0})'.format(hole.name) for hole in holes),
                        apply_args=apply_args,
                        priv='\n  '.join('SCHEMETYPE_T {0};'.format(hole.name) for hole in holes),
                        ),
                dedent('''\
                    THUNK_T {cls}::operator()({apply_args}) const {{
                      {decls}
                      {ops}
                      {body}
                    }}''').format(
                        cls=exp.name,
                        apply_args=apply_args,
                        decls=decls,
                        ops=ops,
                        body='return {0};'.format(str(exp.bodyExp))
                        )
                )
        return exp.name
    @property
    def decls_ops(self):
        min_nargs = min(self.nargs)
        max_nargs = max(self.nargs)
        decl = dedent('''\
            class lambda_t {{
             public:
              {virtuals}
            }};
            ''').format(
                virtuals='\n  '.join(
                    'THUNK_T operator()({0}) const;'.format(
                        ', '.join('SCHEMETYPE_T' for _ in range(i))
                        )
                    for i in range(min_nargs, max_nargs + 1)
                    )
                )
        op = ''.join(
            dedent('''\
                THUNK_T lambda_t::operator()({0}) const {{
                  printf("error: lambda called with an improper number of arguments\\n");
                  exit(-1);
                }}
                ''').format(', '.join('SCHEMETYPE_T _{0}'.format(j) for j in range(i)))
            for i in range(min_nargs, max_nargs + 1)
            )
        if len(self._decls):
            decls, ops = zip(*self._decls.values())
        else:
            decls, ops = [], []
        return (
            decl + '\n'.join(d for d in decls if d),
            op + '\n'.join(o for o in ops if o)
            )

def declare(var, construct=True):
    return 'SCHEMETYPE_T {0}{1};'.format(
        var,
        '(new schemetype_t)' if construct else '')

_tmp = gensym('__halt_')
halt = LamExp(
    [CppCode(VarExp, _tmp.name, [])],
    CppCode(
        LamExp,
        'THUNK_T(nullptr)',
        [(
            'int retval = 0;',
            dedent('''\
                switch({arg}->type) {{
                 case {NUM}:
                  printf("%ld\\n", {arg}->num);
                 case {LAM}:
                  printf("you want to return a lambda?! really?!\\n");
                 case {STR}:
                  printf("%s\\n", {arg}->str->c_str());
                 default:
                  printf("error\\n");
                  retval = -1;
                }}
                exit(retval);''').format(
                    arg=_tmp.name,
                    NUM=NUM, LAM=LAM, STR=STR)
            )]
        )
    )
del _tmp

def gen_cpp(exp):
    # compute the holes at each LamExp
    holes = compute_holes(exp)
    lambda_gen = LambdaGenCpp(exp)

    # map function
    def to_cpp(exp):
        code = None

        decls = []

        if isinstance(exp, VarExp):
            code = exp.name
        elif isinstance(exp, (NumExp, BoolExp, StrExp)):
            if isinstance(exp, NumExp):
                val = str(exp.val)
                sym = '__num_'
                typ = NUM
            elif isinstance(exp, BoolExp):
                val = '1' if exp.val else '0'
                sym = '__bool_'
                typ = NUM
            elif isinstance(exp, StrExp):
                val = '"{0}"'.format(exp.val)
                sym = '__str_'
                typ = STR
            else:
                assert(0)
            tmp = gensym(sym)
            decl = (
                declare(tmp),
                dedent('''\
                    {var}->type = {typ};
                    {var}->{loc} = {val};''').format(
                        var=tmp.name,
                        loc=typ.lower(),
                        val=val,
                        typ=typ.upper()
                        )
                )
            decls.append(decl)
            code = tmp.name
        elif isinstance(exp, VoidExp):
            unimplemented(exp)
        elif isinstance(exp, LamExp):
            cls = lambda_gen[exp]
            # instantiate a temporary to fill with our lambda
            tmp = gensym('__lam_')
            decl = (
                declare(tmp),
                '{var}->lam = LAMBDA_T(new {cls}({holes}));'.format(
                    var=tmp.name,
                    cls=cls,
                    holes=', '.join(hole.name for hole in holes[exp])
                    )
                )
            decls.append(decl)
            code = tmp.name
        elif isinstance(exp, AppExp):
            for arg in exp.argExps:
                decls.extend(arg.decls)
            decls.extend(exp.funcExp.decls)
            tmp = gensym('__ret_')
            func = str(exp.funcExp)
            if is_primop(func):
                prim = gensym('__prim_')
                typ, body = gen_primop(func, prim, *[str(arg) for arg in exp.argExps[:-1]])
                decl = (
                    declare(prim) + '\nTHUNK_T {0}(nullptr);'.format(tmp.name),
                    dedent('''\
                        {prim}->type = {typ};
                        {body}
                        {var} = std::move((*{func}->lam)({prim}));''').format(
                            prim=prim.name,
                            body=body,
                            typ=typ,
                            var=tmp.name,
                            func=str(exp.argExps[-1])
                            )
                    )
                decls.append(decl)
            elif exp.funcExp.typ == VarExp:
                decl = (
                    'THUNK_T {0}(nullptr);'.format(tmp.name),
                    '{var} = std::move((*{func}->lam)({args}));'.format(
                            var=tmp.name,
                            func=str(exp.funcExp),
                            narg=len(exp.argExps),
                            args=', '.join(str(arg) for arg in exp.argExps)
                            )
                    )
                decls.append(decl)
            else:
                raise RuntimeError('AppExp unimplemented for funcExp of type: {0}'.format(str(exp.funcExp.typ)))
            code = tmp.name
        elif isinstance(exp, IfExp):
            decls.extend(exp.condExp.decls)
            then_decls, then_ops = exp.thenExp.decls_ops
            else_decls, else_ops = exp.elseExp.decls_ops
            tmp = gensym('__ret_')
            decl = (
                'THUNK_T {0}(nullptr);'.format(tmp.name),
                dedent('''\
                    if ({cond}->num) {{
                      {then_decls}
                      {then_ops}
                      {var} = std::move({then});
                    }}
                    else {{
                      {else_decls}
                      {else_ops}
                      {var} = std::move({else_});
                    }}''').format(
                        var=tmp.name,
                        cond=str(exp.condExp),
                        then_decls=then_decls,
                        then_ops=then_ops,
                        then=str(exp.thenExp),
                        else_decls=else_decls,
                        else_ops=else_ops,
                        else_=str(exp.elseExp)
                        )
                )
            decls.append(decl)
            code = tmp.name
        elif isinstance(exp, LetRecExp):
            for var, body in exp.bindings:
                decls.extend(body.decls)
                decl = (
                    declare(var),
                    '{var} = std::move({body});'.format(
                        var=str(var),
                        body=str(body)
                        )
                    )
                decls.append(decl)
            decls.extend(exp.bodyExp.decls)
            code = str(exp.bodyExp)
        elif isinstance(exp, BeginExp):
            unimplemented(exp)
        elif isinstance(exp, SetExp):
            unimplemented(exp)
        elif isinstance(exp, SetThenExp):
            unimplemented(exp)
        elif isinstance(exp, CppCode):
            return exp
        else:
            unimplemented(exp)
        return CppCode(type(exp), code, decls)

    body = exp.map(to_cpp)

    main_decls, main_ops = body.decls_ops
    lambda_decls, lambda_ops = lambda_gen.decls_ops

    # generate some C code!
    code = dedent('''\
        #include <cstdio>
        #include <cstdlib>
        #include <memory>
        #include <string>
        enum type_t {{ {types} }};
        // forward decls -----------------------------------------------------------------------------------
        class lambda_t;
        class schemetype_t;
        class thunk_t;
        #define LAMBDA_T std::shared_ptr<lambda_t>
        #define SCHEMETYPE_T std::shared_ptr<schemetype_t>
        #define THUNK_T std::unique_ptr<thunk_t>
        // lambda_t decl -----------------------------------------------------------------------------------
        {lambda_decls}
         // schemetype_t decl -------------------------------------------------------------------------------
        class schemetype_t {{
         public:
          union {{
            long num;
            LAMBDA_T lam;
            std::shared_ptr<std::string> str;
            type_t type;
          }};
          schemetype_t();
          ~schemetype_t();
        }};
       // thunk_t decl ------------------------------------------------------------------------------------
        class thunk_t {{
         public:
          thunk_t(LAMBDA_T);
          ~thunk_t();
          THUNK_T operator()() const;
         private:
          LAMBDA_T next;
        }};
        // lambda_t impl -----------------------------------------------------------------------------------
        {lambda_ops}
        // schemetype_t impl -------------------------------------------------------------------------------
        schemetype_t::schemetype_t() {{ }}
        schemetype_t::~schemetype_t() {{
          lam.reset();
          str.reset();
        }}
        // thunk_t impl ------------------------------------------------------------------------------------
        thunk_t::thunk_t(LAMBDA_T next) : next(next) {{ }}
        thunk_t::~thunk_t() {{ next.reset(); }}
        THUNK_T thunk_t::operator()() const {{ return (*next)(); }}
        // main --------------------------------------------------------------------------------------------
        int main() {{
          {main_decls}
          {main_ops}
          // trampoline
          while (true) {{
            {body} = std::move((*{body})());
          }}
          return 0;
        }}
        ''').format(
            types=', '.join(TYPES),
            lambda_decls=lambda_decls,
            lambda_ops=lambda_ops,
            main_decls=main_decls,
            main_ops=main_ops,
            body=str(body)
            )

    return code

def pretty_cpp(code, nspace=2):
    TERMINATORS = (' ', '\t', ':')
    unindent = nspace // 2
    # prettify code
    pretty_code = code.splitlines()
    indent = 0
    for i, line in enumerate(pretty_code):
        line = line.strip()
        j = 0
        if len(line) and line[0] == '}':
            indent -= 1
            j = 1
        if ((len(line) >= 5 and line[:5].lower() == 'case ') or
            (len(line) >= 6 and line[:6].lower() == 'public'  and line[6] in TERMINATORS) or
            (len(line) >= 7 and line[:7].lower() == 'default' and line[7] in TERMINATORS) or
            (len(line) >= 7 and line[:7].lower() == 'private' and line[7] in TERMINATORS)):
            prefix = ' ' * (nspace * indent - unindent)
        else:
            prefix = ' ' * (nspace * indent)
        pretty_code[i] = prefix + line
        for char in line[j:]:
            if char == '{':
                indent += 1
            elif char == '}':
                indent -= 1
    return '\n'.join(pretty_code)