from rest_framework import serializers

from ares.api.models import User


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ("email", "password", "name", "gender", "birth", "phone_number")
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user


class SocialUserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(required=False, write_only=True, allow_null=True, allow_blank=True)

    class Meta:
        model = User
        fields = ("email", "password", "name", "gender", "birth", "phone_number")
        extra_kwargs = {
            "password": {"write_only": True},
            "email": {"read_only": True},
        }

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "gender",
            "birth",
            "phone_number",
            "date_joined",
        )
        read_only_fields = ("id", "email", "date_joined")
