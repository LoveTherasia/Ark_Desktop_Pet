#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <spine/AnimationState.h>
#include <spine/Atlas.h>
#include <spine/Attachment.h>
#include <spine/Color.h>
#include <spine/ClippingAttachment.h>
#include <spine/MeshAttachment.h>
#include <spine/RegionAttachment.h>
#include <spine/Skeleton.h>
#include <spine/SkeletonBinary.h>
#include <spine/SkeletonClipping.h>
#include <spine/SkeletonData.h>
#include <spine/Slot.h>
#include <spine/SlotData.h>
#include <spine/extension.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

struct PetContext {
	spAtlas* atlas = nullptr;
	spSkeletonData* skeletonData = nullptr;
	spSkeleton* skeleton = nullptr;
	spAnimationStateData* stateData = nullptr;
	spAnimationState* state = nullptr;
	std::vector<float> triangles;
	int clippingAttachmentsSeen = 0;
};

static std::string lastError;

static std::wstring utf8ToWide(const char* value) {
	if (!value) return {};
	const int count = MultiByteToWideChar(CP_UTF8, 0, value, -1, nullptr, 0);
	if (count <= 0) return {};
	std::wstring result(static_cast<size_t>(count), L'\0');
	MultiByteToWideChar(CP_UTF8, 0, value, -1, result.data(), count);
	result.resize(static_cast<size_t>(count - 1));
	return result;
}

static FILE* openUtf8(const char* path, const wchar_t* mode) {
	std::wstring widePath = utf8ToWide(path);
	if (widePath.empty()) return nullptr;
	FILE* file = _wfopen(widePath.c_str(), mode);
	if (file) return file;

	for (wchar_t& character : widePath) {
		if (character == L'#') character = L'_';
	}
	return _wfopen(widePath.c_str(), mode);
}

extern "C" void _spAtlasPage_createTexture(spAtlasPage* page, const char* path) {
	FILE* file = openUtf8(path, L"rb");
	if (!file) {
		lastError = std::string("Could not open atlas texture: ") + (path ? path : "(null)");
		return;
	}

	unsigned char header[24]{};
	const size_t read = std::fread(header, 1, sizeof(header), file);
	std::fclose(file);
	static const unsigned char pngSignature[] = {137, 80, 78, 71, 13, 10, 26, 10};
	if (read != sizeof(header) || std::memcmp(header, pngSignature, sizeof(pngSignature)) != 0) {
		lastError = std::string("Atlas texture is not a readable PNG: ") + path;
		return;
	}

	page->width = static_cast<int>((header[16] << 24) | (header[17] << 16) | (header[18] << 8) | header[19]);
	page->height = static_cast<int>((header[20] << 24) | (header[21] << 16) | (header[22] << 8) | header[23]);
	page->rendererObject = std::malloc(1);
}

extern "C" void _spAtlasPage_disposeTexture(spAtlasPage* page) {
	std::free(page->rendererObject);
	page->rendererObject = nullptr;
}

extern "C" char* _spUtil_readFile(const char* path, int* length) {
	FILE* file = openUtf8(path, L"rb");
	if (!file) return nullptr;

	if (std::fseek(file, 0, SEEK_END) != 0) {
		std::fclose(file);
		return nullptr;
	}
	const long fileLength = std::ftell(file);
	if (fileLength < 0 || fileLength > 0x7fffffffL || std::fseek(file, 0, SEEK_SET) != 0) {
		std::fclose(file);
		return nullptr;
	}

	char* data = static_cast<char*>(std::malloc(static_cast<size_t>(fileLength) + 1));
	if (!data) {
		std::fclose(file);
		return nullptr;
	}
	const size_t read = std::fread(data, 1, static_cast<size_t>(fileLength), file);
	std::fclose(file);
	if (read != static_cast<size_t>(fileLength)) {
		std::free(data);
		return nullptr;
	}
	data[fileLength] = '\0';
	*length = static_cast<int>(fileLength);
	return data;
}

static void appendVertex(
	std::vector<float>& output,
	float x,
	float y,
	float u,
	float v,
	const spColor& color
) {
	output.push_back(x);
	output.push_back(y);
	output.push_back(u);
	output.push_back(v);
	output.push_back(color.r);
	output.push_back(color.g);
	output.push_back(color.b);
	output.push_back(color.a);
}

static spColor combinedColor(const spSkeleton* skeleton, const spSlot* slot, const spColor& attachmentColor) {
	spColor result;
	result.r = skeleton->color.r * slot->color.r * attachmentColor.r;
	result.g = skeleton->color.g * slot->color.g * attachmentColor.g;
	result.b = skeleton->color.b * slot->color.b * attachmentColor.b;
	result.a = skeleton->color.a * slot->color.a * attachmentColor.a;
	return result;
}

static void appendIndexedTriangles(
	std::vector<float>& output,
	const float* vertices,
	const float* uvs,
	const unsigned short* indices,
	int indexCount,
	const spColor& color,
	int blendMode
) {
	for (int index = 0; index + 2 < indexCount; index += 3) {
		for (int corner = 0; corner < 3; ++corner) {
			const int vertex = indices[index + corner];
			const int point = vertex * 2;
			appendVertex(
				output,
				vertices[point],
				vertices[point + 1],
				uvs[point],
				uvs[point + 1],
				color
			);
		}
		output.push_back(static_cast<float>(blendMode));
	}
}

static std::vector<float> makeTriangles(PetContext* context) {
	std::vector<float> output;
	const spSkeleton* skeleton = context->skeleton;
	auto* clipper = spSkeletonClipping_create();
	context->clippingAttachmentsSeen = 0;
	for (int slotIndex = 0; slotIndex < skeleton->slotsCount; ++slotIndex) {
		const spSlot* slot = skeleton->drawOrder[slotIndex];
		const spAttachment* attachment = slot->attachment;
		if (!attachment) {
			spSkeletonClipping_clipEnd(clipper, const_cast<spSlot*>(slot));
			continue;
		}

		if (attachment->type == SP_ATTACHMENT_CLIPPING) {
			if (spSkeletonClipping_clipStart(
				clipper,
				const_cast<spSlot*>(slot),
				const_cast<spClippingAttachment*>(reinterpret_cast<const spClippingAttachment*>(attachment))
			)) {
				++context->clippingAttachmentsSeen;
			}
			continue;
		}

		const int blendMode = slot->data->blendMode;
		if (attachment->type == SP_ATTACHMENT_REGION) {
			const auto* region = reinterpret_cast<const spRegionAttachment*>(attachment);
			float vertices[8]{};
			float uvs[8]{};
			spRegionAttachment_computeWorldVertices(
				const_cast<spRegionAttachment*>(region), slot->bone, vertices, 0, 2
			);
			const unsigned short indices[] = {0, 1, 2, 2, 3, 0};
			/* computeWorldVertices 与 uvs 的角点顺序一致（BL、UL、UR、BR），
			   所以顶点 i 必须直接取 uvs[i]，不能再做重排；否则旋转图集区域会取错纹理。 */
			const int uvIndices[] = {0, 1, 2, 3};
			const spColor color = combinedColor(skeleton, slot, region->color);
			for (int vertex = 0; vertex < 4; ++vertex) {
				uvs[vertex * 2] = region->uvs[uvIndices[vertex] * 2];
				uvs[vertex * 2 + 1] = region->uvs[uvIndices[vertex] * 2 + 1];
			}
			if (spSkeletonClipping_isClipping(clipper)) {
				spSkeletonClipping_clipTriangles(clipper, vertices, 8, const_cast<unsigned short*>(indices), 6, uvs, 2);
				appendIndexedTriangles(
					output,
					clipper->clippedVertices->items,
					clipper->clippedUVs->items,
					clipper->clippedTriangles->items,
					clipper->clippedTriangles->size,
					color,
					blendMode
				);
			} else {
				appendIndexedTriangles(output, vertices, uvs, indices, 6, color, blendMode);
			}
		} else if (attachment->type == SP_ATTACHMENT_MESH || attachment->type == SP_ATTACHMENT_LINKED_MESH) {
			const auto* mesh = reinterpret_cast<const spMeshAttachment*>(attachment);
			const int vertexCount = mesh->super.worldVerticesLength / 2;
			std::vector<float> vertices(static_cast<size_t>(vertexCount) * 2);
			spVertexAttachment_computeWorldVertices(
				const_cast<spVertexAttachment*>(&mesh->super),
				const_cast<spSlot*>(slot),
				0,
				mesh->super.worldVerticesLength,
				vertices.data(),
				0,
				2
			);
			const spColor color = combinedColor(skeleton, slot, mesh->color);
			if (spSkeletonClipping_isClipping(clipper)) {
				spSkeletonClipping_clipTriangles(
					clipper,
					vertices.data(),
					mesh->super.worldVerticesLength,
					mesh->triangles,
					mesh->trianglesCount,
					mesh->uvs,
					2
				);
				appendIndexedTriangles(
					output,
					clipper->clippedVertices->items,
					clipper->clippedUVs->items,
					clipper->clippedTriangles->items,
					clipper->clippedTriangles->size,
					color,
					blendMode
				);
			} else {
				appendIndexedTriangles(
					output,
					vertices.data(),
					mesh->uvs,
					mesh->triangles,
					mesh->trianglesCount,
					color,
					blendMode
				);
			}
		}
		spSkeletonClipping_clipEnd(clipper, const_cast<spSlot*>(slot));
	}
	spSkeletonClipping_clipEnd2(clipper);
	spSkeletonClipping_dispose(clipper);
	return output;
}

extern "C" __declspec(dllexport) PetContext* pet_create(const char* skeletonPath, const char* atlasPath) {
	lastError.clear();
	auto* context = new PetContext();
	context->atlas = spAtlas_createFromFile(atlasPath, nullptr);
	if (!context->atlas || !lastError.empty()) {
		if (lastError.empty()) lastError = "Failed to load the Spine atlas.";
		if (context->atlas) spAtlas_dispose(context->atlas);
		delete context;
		return nullptr;
	}

	spSkeletonBinary* binary = spSkeletonBinary_create(context->atlas);
	if (!binary) {
		lastError = "Failed to create the Spine binary reader.";
		spAtlas_dispose(context->atlas);
		delete context;
		return nullptr;
	}
	context->skeletonData = spSkeletonBinary_readSkeletonDataFile(binary, skeletonPath);
	if (!context->skeletonData) {
		lastError = binary->error ? binary->error : "Failed to parse the Spine skeleton.";
		spSkeletonBinary_dispose(binary);
		spAtlas_dispose(context->atlas);
		delete context;
		return nullptr;
	}
	spSkeletonBinary_dispose(binary);

	context->skeleton = spSkeleton_create(context->skeletonData);
	context->stateData = spAnimationStateData_create(context->skeletonData);
	context->state = spAnimationState_create(context->stateData);
	if (!context->skeleton || !context->stateData || !context->state) {
		lastError = "Failed to initialize the Spine animation state.";
		if (context->state) spAnimationState_dispose(context->state);
		if (context->skeleton) spSkeleton_dispose(context->skeleton);
		if (context->stateData) spAnimationStateData_dispose(context->stateData);
		spSkeletonData_dispose(context->skeletonData);
		spAtlas_dispose(context->atlas);
		delete context;
		return nullptr;
	}

	spSkeleton_setToSetupPose(context->skeleton);
	spSkeleton_updateWorldTransform(context->skeleton);
	context->triangles = makeTriangles(context);
	return context;
}

extern "C" __declspec(dllexport) void pet_destroy(PetContext* context) {
	if (!context) return;
	spAnimationState_dispose(context->state);
	spSkeleton_dispose(context->skeleton);
	spAnimationStateData_dispose(context->stateData);
	spSkeletonData_dispose(context->skeletonData);
	spAtlas_dispose(context->atlas);
	delete context;
}

extern "C" __declspec(dllexport) const char* pet_last_error() {
	return lastError.c_str();
}

extern "C" __declspec(dllexport) int pet_animation_count(const PetContext* context) {
	return context && context->skeletonData ? context->skeletonData->animationsCount : 0;
}

extern "C" __declspec(dllexport) const char* pet_animation_name(const PetContext* context, int index) {
	if (!context || index < 0 || index >= context->skeletonData->animationsCount) return "";
	return context->skeletonData->animations[index]->name;
}

extern "C" __declspec(dllexport) int pet_set_animation(PetContext* context, const char* name, int loop) {
	if (!context || !name) return 0;
	return spAnimationState_setAnimationByName(context->state, 0, name, loop) != nullptr;
}

extern "C" __declspec(dllexport) void pet_update(PetContext* context, float delta) {
	if (!context) return;
	spAnimationState_update(context->state, std::max(0.0f, std::min(delta, 0.1f)));
	spAnimationState_apply(context->state, context->skeleton);
	spSkeleton_updateWorldTransform(context->skeleton);
	// Cache once per update; the UI queries both the size and vertex bytes while painting.
	context->triangles = makeTriangles(context);
}

extern "C" __declspec(dllexport) int pet_triangle_float_count(const PetContext* context) {
	return context ? static_cast<int>(context->triangles.size()) : 0;
}

/* 诊断用：导出骨骼数量、名称与当前世界坐标，便于和 spine-ts 逐骨骼比对。 */
extern "C" __declspec(dllexport) int pet_bone_count(const PetContext* context) {
	return context && context->skeleton ? context->skeleton->bonesCount : 0;
}

extern "C" __declspec(dllexport) const char* pet_bone_name(const PetContext* context, int index) {
	if (!context || !context->skeleton || index < 0 || index >= context->skeleton->bonesCount) return "";
	const char* name = context->skeleton->bones[index]->data->name;
	return name ? name : "";
}

extern "C" __declspec(dllexport) int pet_bone_transform(const PetContext* context, int index, float* output) {
	if (!context || !context->skeleton || !output || index < 0 || index >= context->skeleton->bonesCount) return 0;
	const spBone* bone = context->skeleton->bones[index];
	output[0] = bone->worldX;
	output[1] = bone->worldY;
	output[2] = bone->rotation;
	output[3] = bone->scaleX;
	output[4] = bone->scaleY;
	output[5] = bone->a;
	output[6] = bone->b;
	output[7] = bone->c;
	output[8] = bone->d;
	return 9;
}

extern "C" __declspec(dllexport) void pet_update_world_transform(PetContext* context) {
	if (!context || !context->skeleton) return;
	spSkeleton_updateWorldTransform(context->skeleton);
}

/* 诊断用：导出每个插槽当前的世界坐标顶点，便于与 spine-ts 逐附件比对。 */
extern "C" __declspec(dllexport) int pet_slot_count(const PetContext* context) {
	return context && context->skeleton ? context->skeleton->slotsCount : 0;
}

extern "C" __declspec(dllexport) const char* pet_slot_name(const PetContext* context, int index) {
	if (!context || !context->skeleton || index < 0 || index >= context->skeleton->slotsCount) return "";
	const char* name = context->skeleton->slots[index]->data->name;
	return name ? name : "";
}

/* 返回该插槽附件在世界空间下的顶点数（每顶点 2 个 float），0 表示没有可导出的附件。 */
extern "C" __declspec(dllexport) int pet_slot_vertex_count(const PetContext* context, int index) {
	if (!context || !context->skeleton || index < 0 || index >= context->skeleton->slotsCount) return 0;
	spSlot* slot = context->skeleton->slots[index];
	const spAttachment* attachment = slot->attachment;
	if (!attachment) return 0;
	if (attachment->type == SP_ATTACHMENT_REGION) return 4;
	if (attachment->type == SP_ATTACHMENT_MESH || attachment->type == SP_ATTACHMENT_LINKED_MESH) {
		const auto* mesh = reinterpret_cast<const spMeshAttachment*>(attachment);
		return mesh->super.worldVerticesLength / 2;
	}
	return 0;
}

extern "C" __declspec(dllexport) int pet_slot_vertices(
	const PetContext* context,
	int index,
	float* output,
	int capacity
) {
	const int count = pet_slot_vertex_count(context, index);
	if (count <= 0 || !output || capacity < count * 2) return 0;
	spSlot* slot = context->skeleton->slots[index];
	const spAttachment* attachment = slot->attachment;
	if (attachment->type == SP_ATTACHMENT_REGION) {
		const auto* region = reinterpret_cast<const spRegionAttachment*>(attachment);
		spRegionAttachment_computeWorldVertices(
			const_cast<spRegionAttachment*>(region), slot->bone, output, 0, 2
		);
		return count;
	}
	const auto* mesh = reinterpret_cast<const spMeshAttachment*>(attachment);
	spVertexAttachment_computeWorldVertices(
		const_cast<spVertexAttachment*>(&mesh->super),
		slot,
		0,
		mesh->super.worldVerticesLength,
		output,
		0,
		2
	);
	return count;
}

extern "C" __declspec(dllexport) int pet_clipping_attachment_count(const PetContext* context) {
	return context ? context->clippingAttachmentsSeen : 0;
}

extern "C" __declspec(dllexport) int pet_copy_triangles(
	const PetContext* context,
	float* output,
	int capacity
) {
	if (!context || !output || capacity <= 0) return 0;
	const int count = std::min(capacity, static_cast<int>(context->triangles.size()));
	std::memcpy(output, context->triangles.data(), static_cast<size_t>(count) * sizeof(float));
	return count;
}

/* 诊断用：导出某个区域附件四个顶点各自对应的 UV，用于验证顶点与 UV 的配对关系。
   顺序与 spRegionAttachment_computeWorldVertices 一致（BL、UL、UR、BR）。 */
extern "C" __declspec(dllexport) int pet_region_uvs(
	const PetContext* context,
	int index,
	float* output,
	int capacity
) {
	if (!context || !context->skeleton || !output || capacity < 8) return 0;
	if (index < 0 || index >= context->skeleton->slotsCount) return 0;
	const spAttachment* attachment = context->skeleton->slots[index]->attachment;
	if (!attachment || attachment->type != SP_ATTACHMENT_REGION) return 0;
	const auto* region = reinterpret_cast<const spRegionAttachment*>(attachment);
	for (int vertex = 0; vertex < 4; ++vertex) {
		output[vertex * 2] = region->uvs[vertex * 2];
		output[vertex * 2 + 1] = region->uvs[vertex * 2 + 1];
	}
	return 4;
}

