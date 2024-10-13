class Adapter:
    def __call__(self, lm, lm_kwargs, signature, demos, inputs, _parse_values=True):
        inputs = self.format(signature, demos, inputs)
        inputs = dict(prompt=inputs) if isinstance(inputs, str) else dict(messages=inputs)

        # print(lm.model)
        outputs = lm(**inputs, **lm_kwargs)
        values = []

        for output in outputs:
            try:
                value = self.parse(signature, output, _parse_values=_parse_values)
                assert set(value.keys()) == set(signature.output_fields.keys()), f"Expected {signature.output_fields.keys()} but got {value.keys()}"
                values.append(value)
            except Exception as e:
                # print(f"Error parsing output: {output} for input: {inputs}")
                raise e
        
        return values
