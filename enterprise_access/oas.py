# from drf_yasg import openapi
#
#
# class CustomSchemaGenerator(openapi.SchemaGenerator):
#     def get_schema(self, *args, **kwargs):
#         schema = super().get_schema(*args, **kwargs)
#
#         # Convert oneOf fields to enums
#         for path, path_data in schema['paths'].items():
#             for method, method_data in path_data.items():
#                 for parameter in method_data.get('parameters', []):
#                     pass
#                     # if 'oneOf' in parameter:
#                     #     from pdb import set_trace; set_trace()
#                     #     parameter['enum'] = parameter.pop('oneOf')
#                     #     del parameter['oneOf']
#
#         return schema
